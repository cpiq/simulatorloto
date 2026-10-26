"""Loto 6/49 - port Python al motorului unic publicat: 649-coverage-v2.

Necesita Python 3.10+
Instalare: pip install requests beautifulsoup4 tzdata
Rulare: python perplexitymix.py
Optional: --n 12 --seed 649 --extra 3 --backtest 500 --json rezultat.json
Jupyter: ruleaza celula direct (valorile implicite) sau main(["--n", "12"]).
Argumentele interne ale kernelului sunt ignorate numai la rularea directa in notebook.
Pentru aceeasi ultima data istorica, seed si parametri, biletele si
extinderile sunt identice cu motorul JavaScript de pe site.
Generarea este uniforma; primele opt bilete sunt disjuncte. Calibrarea,
frecventa, perechile si profilul sunt descriptive sau diagnostice.
Corectarea rezultatelor vechi nu schimba biletele ciclului curent.
Nu exista avantaj predictiv garantat.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from loto_archive_parser import (
    ARCHIVE_URLS_649, LotoDataExtractionError, archive_request_url,
    extract_draws_from_html, normalize_draws, valid_draw,
)

ENGINE_VERSION = "649-coverage-v2"
GENERATION_MODE = "uniform_coverage"
EVALUATION_VERSION = "649-evaluation-v2"
P_NUMBER, P_PAIR, PRIOR_DRAWS, MAX_SHARED = 6 / 49, 30 / (49 * 48), 400, 2
MASK = 0xFFFFFFFF
UNIVERSE = list(range(1, 50))


@dataclass
class LotoConfig:
    archive_urls: tuple = ARCHIVE_URLS_649
    request_timeout: int = 30
    sleep_between_requests: float = 1.0
    seed: int = 649
    n_bilete: int = 6
    extra_9: int = 1  # 6 + 1 = 7; accepta 0..10 numere suplimentare


def build_session():
    session = requests.Session()
    retry = Retry(total=5, connect=5, read=5, backoff_factor=2,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "HEAD"])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0 Loto649/coverage-v2",
                            "Accept": "text/html", "Cache-Control": "no-cache"})
    return session


def download_all_loto_data(config):
    rows = []
    with build_session() as session:
        print("Descarc istoricul complet din cele sapte arhive...")
        for index, url in enumerate(config.archive_urls, 1):
            if index > 1:
                time.sleep(config.sleep_between_requests)
            try:
                response = session.get(archive_request_url(url, time.time_ns()), timeout=config.request_timeout)
                response.raise_for_status()
                parsed = extract_draws_from_html(response.text, response.url)
            except requests.RequestException as exc:
                raise LotoDataExtractionError(
                    f"Arhiva indisponibila: {url}. Generarea cu istoric partial este oprita."
                ) from exc
            if not parsed:
                raise LotoDataExtractionError(f"Nu s-au extras date din {url}.")
            print(f"[{index}/{len(config.archive_urls)}] {len(parsed)} extrageri")
            rows.extend(parsed)
    draws = normalize_draws(rows)
    if len(draws) < 30:
        raise LotoDataExtractionError("Istoric insuficient pentru generare.")
    return draws


def random649(seed):
    """Mulberry32: operatiile pe 32 biti reproduc Math.imul / >>> din JS."""
    state = seed & MASK
    def random():
        nonlocal state
        state = (state + 0x6D2B79F5) & MASK
        value = ((state ^ (state >> 15)) * (state | 1)) & MASK
        value ^= (value + (((value ^ (value >> 7)) * (value | 61)) & MASK)) & MASK
        return ((value ^ (value >> 14)) & MASK) / 4294967296
    return random


def hash_text(text):
    value = 2166136261
    for character in text:
        value = ((value ^ ord(character)) * 16777619) & MASK
    return value


def history_hash(draws):
    text = ";".join(f"{d['date']}:{','.join(map(str, d['numbers']))}" for d in draws)
    return hash_text(text)


def accumulator(draws=()):
    state = {"total": 0, "frequency": [0] * 50, "pairs": [0] * 2500,
             "lastSeen": [-1] * 50}
    for draw in draws:
        append(state, draw)
    return state


def append(state, draw):
    for n in draw["numbers"]:
        state["frequency"][n] += 1
        state["lastSeen"][n] = state["total"]
    for a, b in itertools.combinations(sorted(draw["numbers"]), 2):
        state["pairs"][a * 50 + b] += 1
    state["total"] += 1


def js_sum(values):
    # Python 3.12 compenseaza sum(float); JS reduce aduna secvential.
    total = 0
    for value in values:
        total += value
    return total


def mean(values):
    return js_sum(values) / max(1, len(values))


def accepted_improvement(values):
    average = mean(values)
    variance = js_sum((value - average) ** 2 for value in values) / max(1, len(values) - 1)
    standard_error = math.sqrt(variance / max(1, len(values)))
    return average, len(values) >= 60 and average > max(1e-9, 3 * standard_error)


def day_groups(draws, start=0):
    cursor = start
    while cursor < len(draws):
        end = cursor + 1
        while end < len(draws) and draws[end]["date"] == draws[cursor]["date"]:
            end += 1
        yield cursor, end
        cursor = end


def calibrate649(draws):
    result = {"frequencyInfluence": 0, "pairInfluence": 0, "delayInfluence": 0,
              "filter": False, "validationDraws": 0, "validationFrom": None,
              "validationTo": None, "criterion": "Brier, avantaj > 3 erori standard"}
    if len(draws) < 260:
        return result
    start = max(200, len(draws) - 200)
    while start > 0 and draws[start - 1]["date"] == draws[start]["date"]:
        start -= 1
    state = accumulator(draws[:start])
    lambdas = [0.25, 0.5]
    frequencies, pairs = [[], []], [[], []]
    for cursor, end in day_groups(draws, start):
        for target in draws[cursor:end]:
            winning = set(target["numbers"])
            for index, influence in enumerate(lambdas):
                freq_delta = pair_delta = 0.0
                for n in UNIVERSE:
                    posterior = (state["frequency"][n] + PRIOR_DRAWS * P_NUMBER) / (state["total"] + PRIOR_DRAWS)
                    probability = (1 - influence) * P_NUMBER + influence * posterior
                    y = int(n in winning)
                    freq_delta += (P_NUMBER - y) ** 2 - (probability - y) ** 2
                frequencies[index].append(freq_delta)
                for a, b in itertools.combinations(UNIVERSE, 2):
                    posterior = (state["pairs"][a * 50 + b] + PRIOR_DRAWS * P_PAIR) / (state["total"] + PRIOR_DRAWS)
                    probability = (1 - influence) * P_PAIR + influence * posterior
                    y = int(a in winning and b in winning)
                    pair_delta += (P_PAIR - y) ** 2 - (probability - y) ** 2
                pairs[index].append(pair_delta)
        for draw in draws[cursor:end]:
            append(state, draw)
    def choose(samples):
        candidates = []
        for influence, values in zip(lambdas, samples):
            average, accepted = accepted_improvement(values)
            if accepted:
                candidates.append((average, influence))
        return max(candidates, key=lambda item: item[0])[1] if candidates else 0
    result.update(frequencyInfluence=choose(frequencies), pairInfluence=choose(pairs),
                  validationDraws=len(draws) - start, validationFrom=draws[start]["date"],
                  validationTo=draws[-1]["date"])
    return result


def draw_profile(numbers):
    odd = sum(n % 2 for n in numbers)
    low = sum(n <= 24 for n in numbers)
    return {"sum": sum(numbers), "max": max(numbers), "odd": odd, "even": 6 - odd,
            "shape": "low-heavy" if low >= 4 else "high-heavy" if low <= 2 else "balanced"}


def sum_bucket(total):
    return "<120" if total < 120 else "120-149" if total < 150 else "150-179" if total < 180 else ">=180"


def max_zone(maximum):
    return "<=39" if maximum <= 39 else "40-44" if maximum <= 44 else "45-49"


def mode(values):
    counts = Counter(values)
    value, count = sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))[0]
    return value, count / len(values)


def model_from_state(state, config):
    scores = [0.0] * 50
    for n in UNIVERSE:
        posterior = (state["frequency"][n] + PRIOR_DRAWS * P_NUMBER) / (state["total"] + PRIOR_DRAWS)
        scores[n] = (1 - config["frequencyInfluence"]) * P_NUMBER + config["frequencyInfluence"] * posterior
    if config["delayInfluence"]:
        delays = [0] + [state["total"] - state["lastSeen"][n] for n in UNIVERSE]
        for n in UNIVERSE:
            scores[n] = ((1 - config["delayInfluence"]) * scores[n]
                         + config["delayInfluence"] * 6 * delays[n] / sum(delays))
    return {**state, "scores": scores, "config": config}


def analyse649(draws, fixed_config=None):
    if not draws:
        raise LotoDataExtractionError("Istoricul 6/49 este gol.")
    for draw in draws:
        _assert_draw649(draw)
    config = fixed_config if fixed_config is not None else calibrate649(draws)
    model = model_from_state(accumulator(draws), config)
    ranked = sorted(UNIVERSE, key=lambda n: (-model["frequency"][n], n))
    model["top_numbers"] = [{"n": n, "score": model["scores"][n], "freq": model["frequency"][n],
                             "delay": model["total"] - 1 - model["lastSeen"][n]} for n in ranked[:12]]
    model["top_pairs"] = sorted(
        [{"a": a, "b": b, "count": model["pairs"][a * 50 + b], "expected": model["total"] * P_PAIR,
          "regularised_ratio": (model["pairs"][a * 50 + b] + PRIOR_DRAWS * P_PAIR) / ((model["total"] + PRIOR_DRAWS) * P_PAIR)}
         for a, b in itertools.combinations(UNIVERSE, 2)],
        key=lambda pair: (-pair["count"], pair["a"], pair["b"]))[:8]
    profiles = [draw_profile(d["numbers"]) for d in draws]
    target = {}
    for name, values in (
        ("sum", [sum_bucket(p["sum"]) for p in profiles]),
        ("max", [max_zone(p["max"]) for p in profiles]),
        ("shape", [p["shape"] for p in profiles]),
        ("ip", [f"{p['odd']}/{p['even']}" for p in profiles]),
    ):
        target[name], target[name + "_p"] = mode(values)
    model.update(target_profile=target, historySeed=history_hash(draws),
                 cycle_date=max(draw["date"] for draw in draws))
    return model


def sample(numbers, count, scores, random):
    available, chosen = list(numbers), []
    while len(chosen) < count:
        threshold = random() * js_sum(scores[n] for n in available)
        index = len(available) - 1
        for i, n in enumerate(available):
            threshold -= scores[n]
            if threshold < 0:
                index = i
                break
        chosen.append(available.pop(index))
    return sorted(chosen)


def common(a, b):
    return len(set(a).intersection(b))


def valid_profile(ticket):
    return (97.5 <= sum(ticket) <= 202.5 and 2 <= sum(n % 2 for n in ticket) <= 4
            and 2 <= sum(n <= 24 for n in ticket) <= 4)


def pair_support(ticket, model):
    if not model["config"]["pairInfluence"]:
        return 0
    score = 0.0
    for a, b in itertools.combinations(ticket, 2):
        ratio = (model["pairs"][a * 50 + b] + PRIOR_DRAWS * P_PAIR) / ((model["total"] + PRIOR_DRAWS) * P_PAIR)
        score += max(-1, min(1, ratio - 1))
    return model["config"]["pairInfluence"] * score / 15


def check_options(n, seed, extra):
    if type(n) is not int or not 1 <= n <= 50:
        raise ValueError("Numarul de bilete trebuie sa fie intre 1 si 50.")
    if type(seed) is not int or not 0 <= seed <= MASK:
        raise ValueError("Seed-ul trebuie sa fie intre 0 si 4294967295.")
    if type(extra) is not int or not 0 <= extra <= 10:
        raise ValueError("Extinderea trebuie sa fie intre 0 si 10 numere suplimentare.")


def permutation649(numbers, random):
    shuffled = list(numbers)
    for i in range(len(shuffled) - 1, 0, -1):
        j = math.floor(random() * (i + 1))
        shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
    return shuffled


def remaining_coverage649(shuffled, random):
    """48 linii afine ramase; oricare doua au cel mult un punct comun."""
    def cell(x, y):
        return shuffled[y * 6 + x if x < 6 else 42 + y if y < 6 else 48]
    lines = [[cell(x, y) for y in range(7)] for x in range(6)]
    for slope in range(1, 7):
        for intercept in range(7):
            lines.append([cell(x, (slope * x + intercept) % 7) for x in range(7)])
    result = []
    for line in permutation649(lines, random):
        omit = math.floor(random() * len(line))
        result.append(sorted(number for index, number in enumerate(line) if index != omit))
    return result


def _generate_pack649(model, n=6, seed=649, extra9=1, diagnostic=False):
    check_options(n, seed, extra9)
    cycle_date = model.get("cycle_date")
    if not _valid_day(cycle_date):
        raise ValueError("Generarea necesita data ultimei extrageri istorice valide.")
    cycle = f"{ENGINE_VERSION}|{cycle_date}|{seed}"
    random = random649(hash_text(f"{cycle}|base"))
    scores = model["scores"] if diagnostic else [1] * 50
    shuffled = None if diagnostic else permutation649(UNIVERSE, random)
    remaining = None if diagnostic else remaining_coverage649(shuffled, random)
    selected, union = [], set()
    for index in range(n):
        best, quality = None, -math.inf
        if not diagnostic:
            best = sorted(shuffled[index * 6:index * 6 + 6]) if index < 8 else remaining[index - 8]
        else:
            for _ in range(128):
                ticket = sample(UNIVERSE, 6, scores, random)
                if any(common(previous, ticket) > MAX_SHARED for previous in selected):
                    continue
                if model["config"]["filter"] and not valid_profile(ticket):
                    continue
                fresh = sum(number not in union for number in ticket)
                overlap = sum(common(previous, ticket) for previous in selected)
                candidate_quality = fresh * 10 - overlap + pair_support(ticket, model)
                if candidate_quality > quality:
                    best, quality = ticket, candidate_quality
                if fresh == 6 and not model["config"]["pairInfluence"]:
                    break
        if best is None:
            raise ValueError("Nu s-a gasit un pachet cu suprapunere de maximum 2. Foloseste alt seed sau mai putine bilete.")
        selected.append(best)
        union.update(best)
    tickets = []
    for nums in selected:
        profile = draw_profile(nums)
        tickets.append({"nums": nums, "sum": profile["sum"],
                        "ip": f"{profile['odd']}/{profile['even']}", "shape": profile["shape"]})
    extensions = []
    for index, ticket in enumerate(selected):
        extension_random = random649(hash_text(f"{cycle}|extra|{index}"))
        extensions.append(sorted(ticket + sample([number for number in UNIVERSE if number not in ticket],
                                                 extra9, scores, extension_random)))
    overlaps = [common(a, b) for a, b in itertools.combinations(selected, 2)]
    return {"tickets": tickets, "extensions": extensions,
            "generation": {"mode": "diagnostic" if diagnostic else GENERATION_MODE,
                           "cycle_date": cycle_date, "seed": seed,
                           "disjoint_prefix": 0 if diagnostic else min(n, 8)},
            "coverage": {"distinct_numbers": len(union), "unique_triples": n * 20,
                         "mean_overlap": mean(overlaps), "max_overlap": max(overlaps, default=0),
                         "max_allowed_overlap": MAX_SHARED}}


def generate649(model, n=6, seed=649, extra9=1):
    """Productie uniforma; calibrarea nu modifica numerele generate."""
    return _generate_pack649(model, n, seed, extra9)


def _valid_day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_numbers649(numbers, minimum=6, maximum=None):
    maximum = minimum if maximum is None else maximum
    return (isinstance(numbers, (list, tuple)) and minimum <= len(numbers) <= maximum
            and all(type(number) is int and 1 <= number <= 49 for number in numbers)
            and len(set(numbers)) == len(numbers))


def _assert_draw649(draw):
    if (not isinstance(draw, dict) or not _valid_numbers649(draw.get("numbers"))
            or "joker" in draw):
        raise ValueError("Evaluarea necesita o extragere 6/49 valida, separata de Joker.")


def _chronological_draws649(draws):
    for draw in draws:
        _assert_draw649(draw)
        if not _valid_day(draw.get("date")):
            raise ValueError("Data extragerii 6/49 este invalida.")
    return sorted(draws, key=lambda draw: (draw["date"], ",".join(map(str, draw["numbers"]))))


def combinations649(n, k=6):
    if type(n) is not int or type(k) is not int or n < 0 or k < 0 or k > n:
        return 0
    return math.comb(n, k)


def _hit_metrics(hits, exact=None, paid=None):
    exact_hits = [hits.count(value) for value in range(7)] if exact is None else exact
    paid = len(hits) if paid is None else paid
    result = {"hits": hits, "best_hits": max((min(6, value) for value in hits), default=0),
              "total_hits": sum(count * hits for hits, count in enumerate(exact_hits)),
              "variants": paid, "paid_variants": paid, "exact_hits": exact_hits}
    result.update({f"variants_ge{hits}": sum(exact_hits[hits:]) for hits in (3, 4, 5, 6)})
    return result


def evaluate649(tickets, draw):
    _assert_draw649(draw)
    if not isinstance(tickets, (list, tuple)) or any(not _valid_numbers649(ticket) for ticket in tickets):
        raise ValueError("Fiecare varianta simpla trebuie sa aiba sase numere distincte intre 1 si 49.")
    return _hit_metrics([common(ticket, draw["numbers"]) for ticket in tickets])


def _combine_systems(systems):
    exact = [sum(system["exact_hits"][hits] for system in systems) for hits in range(7)]
    return {**_hit_metrics([hits for system in systems for hits in system["hits"]], exact,
                          sum(system["paid_variants"] for system in systems)),
            "systems": len(systems), "per_system": systems}


def evaluateSystem649(extensions, draw):
    _assert_draw649(draw)
    if (not isinstance(extensions, (list, tuple))
            or any(not _valid_numbers649(ticket, 6, 16) for ticket in extensions)):
        raise ValueError("Un sistem 6/49 trebuie sa contina intre 6 si 16 numere distincte.")
    systems = []
    for ticket in extensions:
        hits = common(ticket, draw["numbers"])
        exact = [combinations649(hits, value) * combinations649(len(ticket) - hits, 6 - value)
                 for value in range(7)]
        systems.append({**_hit_metrics([hits], exact, combinations649(len(ticket))),
                        "size": len(ticket), "matched_numbers": hits, "distinct_numbers": len(ticket)})
    return _combine_systems(systems)


def summary(rows, n, repetitions=1, include_positions=True):
    total_paid = sum(row["paid_variants"] for row in rows)
    result = {"draws": len(rows) / repetitions,
              "mean_hits": mean([row["total_hits"] / row["paid_variants"] if row["paid_variants"] else 0 for row in rows]),
              "mean_best": mean([row["best_hits"] for row in rows]),
              "exact_hits": [sum(row["exact_hits"][hits] for row in rows) / repetitions for hits in range(7)],
              "paid_variants": mean([row["paid_variants"] for row in rows]),
              "total_paid_variants": total_paid / repetitions,
              "mean_distinct": mean([row.get("distinct_numbers", 0) for row in rows]), "repetitions": repetitions}
    for hits in (3, 4, 5, 6):
        winners = sum(row[f"variants_ge{hits}"] for row in rows)
        result[f"draws_ge{hits}"] = sum(row["best_hits"] >= hits for row in rows) / repetitions
        result[f"variants_ge{hits}"] = winners / repetitions
        result[f"rate_ge{hits}"] = winners / total_paid if total_paid else 0
    if include_positions:
        def subset(row, start, end):
            metrics = (_combine_systems(row["per_system"][start:end]) if "per_system" in row
                       else _hit_metrics(row["hits"][start:end]))
            return {**metrics, "distinct_numbers": 0}
        result["positions"] = [{"position": index + 1,
                                **summary([subset(row, index, index + 1) for row in rows if len(row["hits"]) > index],
                                          1, repetitions, False)} for index in range(min(6, n))]
        result["group_3_5"] = summary([subset(row, 2, 5) for row in rows if len(row["hits"]) >= 5], 3, repetitions, False)
    return result


def benchmark(tests, n, seed, extra9=1, diversified=False):
    random, scores, rows, extended_rows = random649(seed ^ 0x35F876), [1] * 50, [], []
    extension_random = random649(seed ^ 0xA519E4)
    for repeat in range(20):
        for target in tests:
            if diversified:
                pack = generate649({"cycle_date": target["training_cutoff"]}, n,
                                   hash_text(f"{seed}|uniform-reference|{repeat}"), extra9)
                tickets, extensions = [ticket["nums"] for ticket in pack["tickets"]], pack["extensions"]
            else:
                tickets, seen = [], set()
                while len(tickets) < n:
                    ticket = sample(UNIVERSE, 6, scores, random)
                    key = tuple(ticket)
                    if key not in seen:
                        seen.add(key)
                        tickets.append(ticket)
                extensions = [ticket + sample([number for number in UNIVERSE if number not in ticket],
                                               extra9, scores, extension_random) for ticket in tickets]
            rows.append({**evaluate649(tickets, target),
                         "distinct_numbers": len(set(itertools.chain.from_iterable(tickets)))})
            if extra9:
                extended_rows.append({**evaluateSystem649(extensions, target),
                                      "distinct_numbers": len(set(itertools.chain.from_iterable(extensions)))})
    return {"simple": summary(rows, n, 20), "extended": summary(extended_rows, n, 20)}


def backtest649(draws, n=6, seed=649, window=500, diagnostics=False, extra9=1):
    draws = _chronological_draws649(draws)
    check_options(n, seed, extra9)
    if type(window) is not int or not 1 <= window <= 500:
        raise ValueError("Fereastra de backtest trebuie sa fie intre 1 si 500.")
    start = max(260, len(draws) - window)
    if start >= len(draws):
        raise ValueError("Sunt necesare cel putin 261 de extrageri pentru validare si test.")
    while start > 0 and draws[start - 1]["date"] == draws[start]["date"]:
        start -= 1
    calibration = calibrate649(draws[:start])
    state = accumulator(draws[:start])
    rows, extended_rows, diagnostic_rows = [], [], {"frequency": [], "delay": [], "filter": []}
    for cursor, end in day_groups(draws, start):
        model = {**model_from_state(state, calibration), "cycle_date": draws[cursor - 1]["date"]}
        generated = generate649(model, n, seed, extra9)
        variants = {}
        if diagnostics:
            for name, change in (("frequency", {"frequencyInfluence": 0.5}),
                                 ("delay", {"delayInfluence": 0.3}), ("filter", {"filter": True})):
                variant = {**model_from_state(state, {**calibration, **change}), "cycle_date": model["cycle_date"]}
                variants[name] = _generate_pack649(variant, n, seed, 0, diagnostic=True)
        for target in draws[cursor:end]:
            rows.append({"date": target["date"], "numbers": target["numbers"], "training_cutoff": model["cycle_date"],
                         **evaluate649([t["nums"] for t in generated["tickets"]], target),
                         "distinct_numbers": generated["coverage"]["distinct_numbers"]})
            if extra9:
                extended_rows.append({"date": target["date"], **evaluateSystem649(generated["extensions"], target),
                                      "distinct_numbers": len(set(itertools.chain.from_iterable(generated["extensions"])))})
            for name, pack in variants.items():
                diagnostic_rows[name].append({**evaluate649([t["nums"] for t in pack["tickets"]], target),
                                              "distinct_numbers": pack["coverage"]["distinct_numbers"]})
        for target in draws[cursor:end]:
            append(state, target)
    reference = benchmark(rows, n, seed, extra9)
    diversified = benchmark(rows, n, seed, extra9, diversified=True)
    return {"engine": ENGINE_VERSION, "generation_mode": GENERATION_MODE, "evaluation_version": EVALUATION_VERSION,
            "n": n, "extra9": extra9, "seed": seed,
            "first_test": rows[0]["date"], "last_test": rows[-1]["date"],
            "calibration": calibration, "calibration_cutoff": draws[start - 1]["date"],
            "result": summary(rows, n), "extended": summary(extended_rows, n),
            "baseline": reference["simple"], "baseline_extended": reference["extended"],
            "diversified_baseline": diversified["simple"], "diversified_baseline_extended": diversified["extended"],
            "diagnostics": {name: summary(r, n) for name, r in diagnostic_rows.items() if r},
            "rows": rows,
            "methodology": "Generare uniforma reproductibila prin seed si ultima zi istorica; primele 8 bilete au numere distincte intre ele; calibrarea influenteaza numai comparatiile diagnostice; date de antrenare numai din zile anterioare; referinte uniforme cu acelasi buget; sistemele extra sunt evaluate separat la costul C(k,6) variante simple fiecare."}


class CombinedAnalyzer:
    """Interfata simpla pentru import; implementarea este motorul unic de pe site."""
    def __init__(self, draws):
        # Accepta si DataFrame-ul din vechiul script, fara dependenta obligatorie pandas.
        if hasattr(draws, "to_dict"):
            draws = [{"date": str(row["data"]), "numbers": [int(row[f"n{i}"]) for i in range(1, 7)]}
                     for row in draws.to_dict("records")]
        self.draws = normalize_draws(draws)
        self.model = None

    def fit(self):
        self.model = analyse649(self.draws)
        return self

    def generate_tickets(self, count=6, seed=649, extra=1):
        if self.model is None:
            raise RuntimeError("Apeleaza fit() inainte de generare.")
        return generate649(self.model, count, seed, extra)

    def report(self):
        if self.model is None:
            raise RuntimeError("Apeleaza fit() inainte de raport.")
        config = self.model["config"]
        print(f"\nMotor: {ENGINE_VERSION}; istoric: {len(self.draws)} extrageri")
        print(f"Interval: {self.draws[0]['date']} - {self.draws[-1]['date']}")
        print(f"Generare uniforma; ciclu={self.model['cycle_date']}; ponderi active=0; filtru oprit")
        print(f"Calibrare descriptiva: frecventa={config['frequencyInfluence']}, perechi={config['pairInfluence']}; nu modifica biletele")
        print(f"Validare: {config['validationDraws']} extrageri; {config['criterion']}")
        print("\n=== FRECVENTE ISTORICE (descriptive) ===")
        for item in self.model["top_numbers"]:
            print(f"{item['n']:2d} | scor={item['score']:.6f} | freq={item['freq']} | delay={item['delay']}")
        print("\n=== PERECHI ISTORICE (descriptive) ===")
        for item in self.model["top_pairs"]:
            print(f"{item['a']:2d}-{item['b']:2d} | aparitii={item['count']}")
        print("\nProfil istoric descriptiv:", self.model["target_profile"])


def main(argv=None):
    # La executarea unei celule, sys.argv apartine kernelului Jupyter.
    # %run si terminalul seteaza sys.argv[0] la script si isi pastreaza optiunile.
    # main([...]) permite parametri expliciti si in notebook.
    if argv is None:
        argv = [] if Path(sys.argv[0]).name == "ipykernel_launcher.py" else sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=6, help="Numar bilete: 1..50 (implicit 6)")
    parser.add_argument("--seed", type=int, default=649, help="Seed: 0..4294967295")
    parser.add_argument("--extra", type=int, default=1, help="Numere suplimentare: 0..10 (implicit 1)")
    parser.add_argument("--history-json", type=Path, help="Istoric local explicit, lista date/numbers; altfel descarca toate arhivele")
    parser.add_argument("--backtest", type=int, metavar="EXTRAGERI", help="Backtest cronologic: 1..500")
    parser.add_argument("--diagnostics", action="store_true", help="Comparatii diagnostice in backtest, fara a schimba motorul")
    parser.add_argument("--json", type=Path, help="Salveaza rezultatul si parametrii in JSON")
    args = parser.parse_args(argv)
    try:
        check_options(args.n, args.seed, args.extra)
        if args.backtest is not None and not 1 <= args.backtest <= 500:
            raise ValueError("Backtestul accepta 1..500 de extrageri.")
        config = LotoConfig(seed=args.seed, n_bilete=args.n, extra_9=args.extra)
        draws = (normalize_draws(json.loads(args.history_json.read_text(encoding="utf-8")))
                 if args.history_json else download_all_loto_data(config))
        analyzer = CombinedAnalyzer(draws).fit()
        analyzer.report()
        pack = analyzer.generate_tickets(args.n, args.seed, args.extra)
        print(f"\n=== {args.n} BILETE (seed {args.seed}) ===")
        for index, ticket in enumerate(pack["tickets"], 1):
            print(f"Bilet {index:2d}: {ticket['nums']} | suma={ticket['sum']} I/P={ticket['ip']} forma={ticket['shape']}")
        print("Acoperire:", pack["coverage"])
        print(f"\n=== EXTINDERI LA {6 + args.extra} NUMERE: {math.comb(6 + args.extra, 6)} variante simple / extindere ===")
        for index, extension in enumerate(pack["extensions"], 1):
            print(f"Extindere {index:2d}: {extension}")
        fingerprint = hashlib.sha256(json.dumps(draws, separators=(",", ":")).encode()).hexdigest()
        output = {"engine": ENGINE_VERSION, "generated_at": datetime.now(ZoneInfo("Europe/Bucharest")).isoformat(),
                  "parameters": {"n": args.n, "seed": args.seed, "extra9": args.extra},
                  "history": {"total_draws": len(draws), "first_date": draws[0]["date"],
                              "last_date": draws[-1]["date"], "sha256": fingerprint},
                  "calibration": analyzer.model["config"], **pack}
        if args.backtest is not None:
            print("\nRulez backtestul cronologic si reperul aleatoriu (20 repetari)...")
            output["backtest"] = backtest649(draws, args.n, args.seed, args.backtest, args.diagnostics, args.extra)
            for key in ("result", "baseline", "diagnostics"):
                print(f"{key}: {output['backtest'][key]}")
        if args.json:
            args.json.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"\nRezultat salvat: {args.json}")
        print("\nAcoperirea nu garanteaza un avantaj predictiv la extrageri independente.")
    except (ValueError, LotoDataExtractionError, OSError, KeyError) as exc:
        parser.exit(1, f"Eroare: {exc}\n")


if __name__ == "__main__":
    main()
