"""Loto 6/49 - port Python al motorului unic publicat: 649-coverage-v1.

Necesita Python 3.10+
Instalare: pip install requests beautifulsoup4 tzdata
Rulare: python perplexitymix.py
Optional: --n 12 --seed 649 --extra 3 --backtest 500 --json rezultat.json
Jupyter: ruleaza celula direct (valorile implicite) sau main(["--n", "12"]).
Argumentele interne ale kernelului sunt ignorate numai la rularea directa in notebook.
Pentru aceleasi extrageri normalizate, seed si parametri, biletele si
extinderile sunt identice cu motorul JavaScript de pe site.
Frecventa si perechile au influenta numai daca trec validarea cronologica.
Intarzierea si profilul sunt descriptive. Nu exista avantaj predictiv garantat.
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
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ENGINE_VERSION = "649-coverage-v1"
P_NUMBER, P_PAIR, PRIOR_DRAWS, MAX_SHARED = 6 / 49, 30 / (49 * 48), 400, 2
MASK = 0xFFFFFFFF
UNIVERSE = list(range(1, 50))


@dataclass
class LotoConfig:
    archive_urls: tuple = (
        "https://www.loto49.ro/arhiva-loto49-1993-2000.php",
        "https://www.loto49.ro/arhiva-loto49-2001-2005.php",
        "https://www.loto49.ro/arhiva-loto49-2006-2010.php",
        "https://www.loto49.ro/arhiva-loto49-2011-2015.php",
        "https://www.loto49.ro/arhiva-loto-6-49-din-perioada-2016-2019.php",
        "https://www.loto49.ro/arhiva-loto-6-49-din-perioada-2020-2023.php",
        "https://www.loto49.ro/arhiva-loto49.php",
    )
    request_timeout: int = 30
    sleep_between_requests: float = 1.0
    seed: int = 649
    n_bilete: int = 6
    extra_9: int = 1  # 6 + 1 = 7; accepta 0..10 numere suplimentare


class LotoDataExtractionError(RuntimeError):
    pass


def build_session():
    session = requests.Session()
    retry = Retry(total=5, connect=5, read=5, backoff_factor=2,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "HEAD"])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0 Loto649/coverage-v1",
                            "Accept": "text/html", "Cache-Control": "no-cache"})
    return session


def valid_draw(numbers):
    return (len(numbers) == 6 and len(set(numbers)) == 6
            and all(type(n) is int and 1 <= n <= 49 for n in numbers))


def normalize_draws(draws):
    """Aceeasi deduplicare si ordonare ca site-ul, inclusiv extrageri speciale."""
    unique = {}
    today = datetime.now(ZoneInfo("Europe/Bucharest")).date().isoformat()
    for draw in draws:
        day = draw["date"]
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise LotoDataExtractionError("Data invalida in istoric.")
        date.fromisoformat(day)
        numbers = sorted(draw["numbers"])
        if not valid_draw(numbers):
            raise LotoDataExtractionError("Extragere invalida in istoric.")
        if day > today:
            raise LotoDataExtractionError("Arhiva contine date viitoare invalide.")
        unique[(day, ",".join(map(str, numbers)))] = {"date": day, "numbers": numbers}
    return [unique[key] for key in sorted(unique)]


def extract_draws_from_html(html):
    # Nu depinde de <tr>: ultima extragere poate fi publicata fara acest tag.
    pattern = re.compile(r"<td\b[^>]*>([\s\S]*?)</td>\s*"
                         r"((?:<td\b[^>]*>[\s\S]*?</td>\s*){6})", re.I)
    rows = []
    for match in pattern.finditer(html):
        day = BeautifulSoup(match[1], "html.parser").get_text().strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        cells = re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", match[2], re.I)
        values = [BeautifulSoup(cell, "html.parser").get_text().strip() for cell in cells]
        if not all(re.fullmatch(r"\d+", value) for value in values):
            continue
        numbers = sorted(map(int, values))
        if valid_draw(numbers):
            rows.append({"date": day, "numbers": numbers})
    return rows


def download_all_loto_data(config):
    rows = []
    with build_session() as session:
        print("Descarc istoricul complet din cele sapte arhive...")
        for index, url in enumerate(config.archive_urls, 1):
            if index > 1:
                time.sleep(config.sleep_between_requests)
            try:
                response = session.get(url, timeout=config.request_timeout)
                response.raise_for_status()
                parsed = extract_draws_from_html(response.text)
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


def history_hash(draws):
    text = ";".join(f"{d['date']}:{','.join(map(str, d['numbers']))}" for d in draws)
    value = 2166136261
    for character in text:
        value = ((value ^ ord(character)) * 16777619) & MASK
    return value


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
    model.update(target_profile=target, historySeed=history_hash(draws))
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


def generate649(model, n=6, seed=649, extra9=1):
    check_options(n, seed, extra9)
    random = random649(seed ^ model["historySeed"])
    extension_random = random649(seed ^ model["historySeed"] ^ 0xA5A5A5A5)
    selected, union = [], set()
    for _ in range(n):
        best, quality = None, -math.inf
        for _ in range(128):
            ticket = sample(UNIVERSE, 6, model["scores"], random)
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
    extensions = [sorted(ticket + sample([n for n in UNIVERSE if n not in ticket], extra9,
                                         model["scores"], extension_random)) for ticket in selected]
    overlaps = [common(a, b) for a, b in itertools.combinations(selected, 2)]
    return {"tickets": tickets, "extensions": extensions,
            "coverage": {"distinct_numbers": len(union), "unique_triples": n * 20,
                         "mean_overlap": mean(overlaps), "max_overlap": max(overlaps, default=0),
                         "max_allowed_overlap": MAX_SHARED}}


def evaluate649(tickets, draw):
    hits = [common(ticket, draw["numbers"]) for ticket in tickets]
    return {"hits": hits, "best_hits": max(hits), "total_hits": sum(hits)}


def summary(rows, n, repetitions=1):
    return {"draws": len(rows) / repetitions,
            "mean_hits": mean([r["total_hits"] / n for r in rows]),
            "mean_best": mean([r["best_hits"] for r in rows]),
            "draws_ge3": sum(r["best_hits"] >= 3 for r in rows) / repetitions,
            "draws_ge4": sum(r["best_hits"] >= 4 for r in rows) / repetitions,
            "mean_distinct": mean([r["distinct_numbers"] for r in rows]), "repetitions": repetitions}


def benchmark(tests, n, seed):
    random, scores, rows = random649(seed ^ 0x35F876), [1] * 50, []
    for _ in range(20):
        for target in tests:
            tickets, seen = [], set()
            while len(tickets) < n:
                ticket = sample(UNIVERSE, 6, scores, random)
                key = tuple(ticket)
                if key not in seen:
                    seen.add(key)
                    tickets.append(ticket)
            rows.append({**evaluate649(tickets, target),
                         "distinct_numbers": len(set(itertools.chain.from_iterable(tickets)))})
    return summary(rows, n, 20)


def backtest649(draws, n=3, seed=649, window=500, diagnostics=False):
    check_options(n, seed, 0)
    if type(window) is not int or not 1 <= window <= 500:
        raise ValueError("Fereastra de backtest trebuie sa fie intre 1 si 500.")
    start = max(260, len(draws) - window)
    if start >= len(draws):
        raise ValueError("Sunt necesare cel putin 261 de extrageri pentru validare si test.")
    while start > 0 and draws[start - 1]["date"] == draws[start]["date"]:
        start -= 1
    calibration = calibrate649(draws[:start])
    state, past = accumulator(draws[:start]), list(draws[:start])
    rows, diagnostic_rows = [], {"frequency": [], "delay": [], "filter": []}
    for cursor, end in day_groups(draws, start):
        model = {**model_from_state(state, calibration), "historySeed": history_hash(past)}
        generated = generate649(model, n, seed, 0)
        variants = {}
        if diagnostics:
            for name, change in (("frequency", {"frequencyInfluence": 0.5}),
                                 ("delay", {"delayInfluence": 0.3}), ("filter", {"filter": True})):
                variant = {**model_from_state(state, {**calibration, **change}), "historySeed": model["historySeed"]}
                variants[name] = generate649(variant, n, seed, 0)
        for target in draws[cursor:end]:
            rows.append({"date": target["date"], "numbers": target["numbers"],
                         **evaluate649([t["nums"] for t in generated["tickets"]], target),
                         "distinct_numbers": generated["coverage"]["distinct_numbers"]})
            for name, pack in variants.items():
                diagnostic_rows[name].append({**evaluate649([t["nums"] for t in pack["tickets"]], target),
                                              "distinct_numbers": pack["coverage"]["distinct_numbers"]})
        for target in draws[cursor:end]:
            append(state, target)
            past.append(target)
    return {"engine": ENGINE_VERSION, "n": n, "seed": seed,
            "first_test": rows[0]["date"], "last_test": rows[-1]["date"],
            "calibration": calibration, "calibration_cutoff": draws[start - 1]["date"],
            "result": summary(rows, n), "baseline": benchmark(draws[start:], n, seed),
            "diagnostics": {name: summary(r, n) for name, r in diagnostic_rows.items() if r},
            "rows": rows,
            "methodology": "Validare inainte de test; configuratie inghetata; antrenare numai pe zile anterioare; acelasi numar de variante simple."}


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
        print(f"Influenta frecventa={config['frequencyInfluence']}, perechi={config['pairInfluence']}, intarziere=0; filtru oprit")
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
            output["backtest"] = backtest649(draws, args.n, args.seed, args.backtest, args.diagnostics)
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