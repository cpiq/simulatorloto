#!/usr/bin/env python3
"""
loto_multi.py - motoarele Loto 5/40 si Joker (5/45 + 1/20), adaptate din
scripturile originale ale utilizatorului (5din40.py si joker.py) ca sa se
integreze in server.py alaturi de motorul Loto 6/49.

Diferente fata de scripturile originale (disclosed catre utilizator):
- Fara pandas: parsare directa in liste de tupluri (acelasi stil ca motorul
  6/49 din server.py), ca sa nu adaugam o dependinta noua.
- Fara np.random global: fiecare generare foloseste o instanta
  np.random.RandomState(seed) primita ca parametru, ca in server.py, pentru
  reproductibilitate si siguranta in cereri concurente (Flask poate procesa
  mai multe cereri in acelasi timp).
- Loto 5/40: modelul original avea 4 ponderi (frecventa, frecventa recenta,
  intarziere, perechi) cu 0.25 fiecare. Ca sa respecte cerinta "0.4 pt
  frecventa, 0.3 pt restul" in mod uniform cu 6/49 si Joker, ponderile
  implicite brute sunt frecventa=0.4, frecventa_recenta=0.3, intarziere=0.3,
  perechi=0.3, apoi NORMALIZATE sa insumeze 1 (exact ca la 6/49). Rezultat:
  ~0.31 / 0.23 / 0.23 / 0.23.
- Joker: scorul celor 5 numere principale foloseste deja 0.4/0.3/0.3 (freq/
  intarziere/perechi) - identic cu 6/49, nicio schimbare. Scorul separat al
  numarului Joker (1-20) avea implicit 0.55/0.45 (freq/intarziere); pentru
  uniformitate am aplicat aceeasi regula (0.4 brut pt frecventa, 0.3 brut pt
  intarziere, normalizate) => ~0.57/0.43, foarte apropiat de valoarea
  originala.

ONEST: Loto 5/40 si Joker sunt procese independente si uniforme, la fel ca
6/49. Metodele de mai jos NU cresc sansa reala de castig - fac doar biletele
sa arate realist statistic, pe baza istoricului real descarcat de pe
loto49.ro (fara date sintetice/inventate).
"""

import re
import time
from collections import Counter, defaultdict
from itertools import combinations

import numpy as np
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TIMEOUT = 30


def build_session():
    s = requests.Session()
    retry = Retry(total=4, connect=4, read=4, backoff_factor=1.5,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "HEAD"])
    ad = HTTPAdapter(max_retries=retry)
    s.mount("http://", ad)
    s.mount("https://", ad)
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.7",
    })
    return s


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def normalize_weights(*raw):
    """Normalizeaza o lista de ponderi brute sa insumeze 1 (ca la 6/49)."""
    raw = [max(0.0, float(x)) for x in raw]
    s = sum(raw)
    if s <= 0:
        n = len(raw)
        return [1.0 / n] * n
    return [x / s for x in raw]


# ============================================================
# LOTO 5/40
# ============================================================

ARCHIVE_URLS_540 = (
    "https://www.loto49.ro/arhiva-superloto.php",
)
SEED_540 = 540
_CACHE_540 = {"ts": 0, "draws": None, "meta": None}
_CACHE_TTL_540 = 1800  # 30 minute

_PAT_540 = re.compile(
    r"(?<!\d)(\d{4}-\d{1,2}-\d{1,2})"
    r"\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})"
    r"\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})(?!\d)"
)


def valid_draw_540(nums):
    """Arhiva loto49.ro afiseaza 6 numere din 1..40 pentru Loto 5/40 (bilet 5 nr)."""
    return len(nums) == 6 and len(set(nums)) == 6 and all(1 <= n <= 40 for n in nums)


def parse_text_540(text):
    rows = []
    for m in _PAT_540.findall(text):
        nums = list(map(int, m[1:]))
        if valid_draw_540(nums):
            rows.append((m[0], nums))
    return rows


def download_all_540():
    session = build_session()
    seen = {}
    per_source = {}
    for url in ARCHIVE_URLS_540:
        try:
            r = session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            rows = parse_text_540(soup.get_text("\n", strip=True))
            if not rows:
                rows = parse_text_540(soup.get_text(" ", strip=True))
            per_source[url] = len(rows)
            for d, nums in rows:
                seen[(d, tuple(nums))] = (d, nums)
        except Exception as e:
            per_source[url] = f"err: {e}"
        time.sleep(0.4)
    items = list(seen.values())
    items.sort(key=lambda x: x[0])
    draws = [nums for _, nums in items]
    meta = {
        "total_draws": len(draws),
        "first_date": items[0][0] if items else None,
        "last_date": items[-1][0] if items else None,
        "per_source": per_source,
    }
    return draws, meta


def get_draws_540(force=False):
    now = time.time()
    if (not force) and _CACHE_540["draws"] and (now - _CACHE_540["ts"] < _CACHE_TTL_540):
        return _CACHE_540["draws"], _CACHE_540["meta"], True
    draws, meta = download_all_540()
    if not draws:
        raise RuntimeError("Nu s-a putut extrage nicio extragere valida Loto 5/40 din arhiva.")
    _CACHE_540.update(ts=now, draws=draws, meta=meta)
    return draws, meta, False


def draw_profile_540(numbers):
    nums = sorted(numbers)
    odd = sum(1 for n in nums if n % 2 == 1)
    even = len(nums) - odd
    low = sum(1 for n in nums if n <= 20)
    high = len(nums) - low
    total_sum = sum(nums)
    mx = max(nums)
    if high - low >= 2:
        shape = "high-heavy"
    elif low - high >= 2:
        shape = "low-heavy"
    else:
        shape = "balanced"
    return {"sum": total_sum, "max": mx, "odd": odd, "even": even, "shape": shape}


def sum_bucket_540(s):
    if s < 90:
        return "<90"
    if s <= 119:
        return "90-119"
    if s <= 149:
        return "120-149"
    return ">=150"


def max_zone_540(mx):
    if mx <= 27:
        return "<=27"
    if mx <= 34:
        return "28-34"
    return "35-40"


class Analyzer540:
    """Port al CombinedLoto540Analyzer, fara pandas si fara np.random global."""

    def __init__(self, draws, recent_window=104):
        self.draws = draws
        self.recent_window = min(recent_window, len(draws))
        self.frequency = {}
        self.recent_frequency = {}
        self.delay = {}
        self.score = {}
        self.pair_counts = Counter()
        self.transition = defaultdict(Counter)
        self.profile_stats = {}

    def compute_frequency(self):
        freq_all = Counter()
        freq_recent = Counter()
        for d in self.draws:
            freq_all.update(d)
        for d in self.draws[-self.recent_window:] if self.recent_window else []:
            freq_recent.update(d)
        self.frequency = {n: freq_all.get(n, 0) for n in range(1, 41)}
        self.recent_frequency = {n: freq_recent.get(n, 0) for n in range(1, 41)}

    def compute_delay(self):
        total = len(self.draws)
        for n in range(1, 41):
            last = None
            for i in range(total - 1, -1, -1):
                if n in self.draws[i]:
                    last = i
                    break
            self.delay[n] = total if last is None else total - 1 - last

    def compute_pairs(self):
        for d in self.draws:
            for pair in combinations(sorted(d), 2):
                self.pair_counts[pair] += 1

    def compute_transition(self):
        profiles = [draw_profile_540(d) for d in self.draws]
        for a, b in zip(profiles[:-1], profiles[1:]):
            self.transition[a["shape"]][b["shape"]] += 1

    def compute_profile_stats(self):
        profiles = [draw_profile_540(d) for d in self.draws]
        sums = Counter(sum_bucket_540(p["sum"]) for p in profiles)
        maxes = Counter(max_zone_540(p["max"]) for p in profiles)
        shapes = Counter(p["shape"] for p in profiles)
        ip = Counter(f'{p["odd"]}/{p["even"]}' for p in profiles)
        n = len(profiles)
        self.profile_stats = {
            "sum": {k: v / n for k, v in sums.items()},
            "max": {k: v / n for k, v in maxes.items()},
            "shape": {k: v / n for k, v in shapes.items()},
            "ip": {k: v / n for k, v in ip.items()},
        }

    def compute_score(self, w_frequency=0.4, w_recent_frequency=0.3, w_delay=0.3, w_pair=0.3):
        f = np.array([self.frequency[n] for n in range(1, 41)], float)
        rf = np.array([self.recent_frequency[n] for n in range(1, 41)], float)
        d = np.array([self.delay[n] for n in range(1, 41)], float)
        pair_aff = np.zeros(40)
        for (a, b), c in self.pair_counts.items():
            pair_aff[a - 1] += c
            pair_aff[b - 1] += c

        def norm(x):
            return np.zeros_like(x) if x.max() == x.min() else (x - x.min()) / (x.max() - x.min())

        fn, rfn, dn, pn = norm(f), norm(rf), norm(d), norm(pair_aff)
        for n in range(1, 41):
            self.score[n] = float(
                w_frequency * fn[n - 1] + w_recent_frequency * rfn[n - 1]
                + w_delay * dn[n - 1] + w_pair * pn[n - 1]
            )

    def fit(self):
        self.compute_frequency()
        self.compute_delay()
        self.compute_pairs()
        self.compute_transition()
        self.compute_profile_stats()
        self.compute_score()
        return self

    def target_profile(self):
        def top(d):
            return max(d.items(), key=lambda kv: kv[1])[0]
        return {
            "sum": top(self.profile_stats["sum"]),
            "max": top(self.profile_stats["max"]),
            "shape": top(self.profile_stats["shape"]),
            "ip": top(self.profile_stats["ip"]),
        }

    def plausible(self, ticket, target):
        p = draw_profile_540(ticket)
        ok = 0
        if p["shape"] == target["shape"]:
            ok += 1
        if abs(p["odd"] - p["even"]) <= 1:
            ok += 1
        if p["max"] >= 28:
            ok += 1
        if 55 <= p["sum"] <= 130:
            ok += 1
        return ok >= 3

    def gen_ticket(self, rng, size=5, pool=14):
        ranked = sorted(self.score.items(), key=lambda x: x[1], reverse=True)[:pool]
        nums = np.array([n for n, _ in ranked])
        w = np.array([s for _, s in ranked], float)
        w = np.ones_like(w) / len(w) if w.sum() == 0 else w / w.sum()
        return sorted(rng.choice(nums, size, replace=False, p=w).tolist())

    def is_diverse(self, candidate, existing, max_overlap=3):
        cs = set(candidate)
        for t in existing:
            if len(cs.intersection(t)) > max_overlap:
                return False
        return True

    def gen_tickets(self, rng, count=4, size=5, pool=14, use_filter=True, max_overlap=3):
        target = self.target_profile()
        seen = set()
        ordered = []
        attempts = 0
        max_attempts = count * 3000
        while len(ordered) < count and attempts < max_attempts:
            t = tuple(self.gen_ticket(rng, size, pool))
            attempts += 1
            if t in seen:
                continue
            if use_filter and not self.plausible(t, target):
                continue
            if not self.is_diverse(t, ordered, max_overlap):
                continue
            seen.add(t)
            ordered.append(t)
        while len(ordered) < count:
            t = tuple(self.gen_ticket(rng, size, pool))
            if t not in seen:
                seen.add(t)
                ordered.append(t)
        return target, [list(t) for t in ordered]

    def extend_to_9(self, rng, ticket5, pool=20, extra=1):
        ranked = sorted(self.score.items(), key=lambda x: x[1], reverse=True)[:pool]
        existing = set(ticket5)
        nums = np.array([n for n, _ in ranked if n not in existing])
        w = np.array([s for n, s in ranked if n not in existing], float)
        if len(nums) < extra:
            rest = [n for n in range(1, 41) if n not in existing and n not in nums.tolist()]
            nums = np.array(nums.tolist() + rest)
            w = np.array(w.tolist() + [0.0] * len(rest))
        w = np.ones_like(w) / len(w) if w.sum() == 0 else w / w.sum()
        extra_nums = rng.choice(nums, extra, replace=False, p=w)
        return sorted(existing.union(extra_nums.tolist()))


# ============================================================
# JOKER (5/45 + 1/20)
# ============================================================

ARCHIVE_URLS_JOKER = (
    "https://www.loto49.ro/arhiva-joker-2000-2005.php",
    "https://www.loto49.ro/arhiva-joker-2006-2010.php",
    "https://www.loto49.ro/arhiva-joker-2011-2015.php",
    "https://www.loto49.ro/arhiva-joker.php",
)
SEED_JOKER = 649
_CACHE_JOKER = {"ts": 0, "draws": None, "jokers": None, "meta": None}
_CACHE_TTL_JOKER = 1800

_PAT_JOKER = re.compile(
    r"(?<!\d)(\d{4}-\d{2}-\d{2})"
    r"\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})"
    r"\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})(?!\d)"
)


def valid_main_draw_joker(nums):
    return len(nums) == 5 and len(set(nums)) == 5 and all(1 <= n <= 45 for n in nums)


def valid_joker_num(j):
    return 1 <= j <= 20


def parse_text_joker(text):
    text = text.replace("+", " ")
    rows = []
    for m in _PAT_JOKER.findall(text):
        main_nums = list(map(int, m[1:6]))
        joker_n = int(m[6])
        if valid_main_draw_joker(main_nums) and valid_joker_num(joker_n):
            rows.append((m[0], main_nums, joker_n))
    return rows


def download_all_joker():
    session = build_session()
    seen = {}
    per_source = {}
    for url in ARCHIVE_URLS_JOKER:
        try:
            r = session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            rows = parse_text_joker(soup.get_text("\n", strip=True))
            if not rows:
                rows = parse_text_joker(soup.get_text(" ", strip=True))
            per_source[url] = len(rows)
            for d, nums, j in rows:
                seen[(d, tuple(nums), j)] = (d, nums, j)
        except Exception as e:
            per_source[url] = f"err: {e}"
        time.sleep(0.4)
    items = list(seen.values())
    items.sort(key=lambda x: x[0])
    draws = [nums for _, nums, _ in items]
    jokers = [j for _, _, j in items]
    meta = {
        "total_draws": len(draws),
        "first_date": items[0][0] if items else None,
        "last_date": items[-1][0] if items else None,
        "per_source": per_source,
    }
    return draws, jokers, meta


def get_draws_joker(force=False):
    now = time.time()
    if (not force) and _CACHE_JOKER["draws"] and (now - _CACHE_JOKER["ts"] < _CACHE_TTL_JOKER):
        return _CACHE_JOKER["draws"], _CACHE_JOKER["jokers"], _CACHE_JOKER["meta"], True
    draws, jokers, meta = download_all_joker()
    if not draws:
        raise RuntimeError("Nu s-a putut extrage nicio extragere valida Joker din arhiva.")
    _CACHE_JOKER.update(ts=now, draws=draws, jokers=jokers, meta=meta)
    return draws, jokers, meta, False


def draw_profile_joker(numbers):
    nums = sorted(numbers)
    odd = sum(1 for n in nums if n % 2 == 1)
    even = 5 - odd
    s = sum(nums)
    mx = max(nums)
    low = sum(1 for n in nums if n <= 22)
    high = 5 - low
    if high - low >= 2:
        shape = "high-heavy"
    elif low - high >= 2:
        shape = "low-heavy"
    else:
        shape = "balanced"
    return {"sum": s, "max": mx, "odd": odd, "even": even, "shape": shape}


def sum_bucket_joker(s):
    if s < 80:
        return "<80"
    if s <= 109:
        return "80-109"
    if s <= 139:
        return "110-139"
    return ">=140"


def max_zone_joker(mx):
    if mx <= 30:
        return "<=30"
    if mx <= 38:
        return "31-38"
    return "39-45"


class AnalyzerJoker:
    """Port al CombinedJokerAnalyzer, fara pandas si fara np.random global."""

    def __init__(self, draws, jokers):
        self.draws = draws
        self.jokers = jokers
        self.frequency = {}
        self.delay = {}
        self.score = {}
        self.j_frequency = {}
        self.j_delay = {}
        self.j_score = {}
        self.pair_counts = Counter()
        self.transition = defaultdict(Counter)
        self.profile_stats = {}

    # ---- numere principale 1..45 ----
    def compute_frequency(self):
        freq = Counter()
        for d in self.draws:
            freq.update(d)
        self.frequency = {n: freq.get(n, 0) for n in range(1, 46)}

    def compute_delay(self):
        total = len(self.draws)
        for n in range(1, 46):
            last = None
            for i in range(total - 1, -1, -1):
                if n in self.draws[i]:
                    last = i
                    break
            self.delay[n] = total if last is None else total - 1 - last

    def compute_pairs(self):
        for d in self.draws:
            ds = sorted(d)
            for i in range(len(ds)):
                for j in range(i + 1, len(ds)):
                    self.pair_counts[(ds[i], ds[j])] += 1

    def compute_transition(self):
        profiles = [draw_profile_joker(d) for d in self.draws]
        for a, b in zip(profiles[:-1], profiles[1:]):
            self.transition[a["shape"]][b["shape"]] += 1

    def compute_profile_stats(self):
        profiles = [draw_profile_joker(d) for d in self.draws]
        sums = Counter(sum_bucket_joker(p["sum"]) for p in profiles)
        maxes = Counter(max_zone_joker(p["max"]) for p in profiles)
        shapes = Counter(p["shape"] for p in profiles)
        ip = Counter(f'{p["odd"]}/{p["even"]}' for p in profiles)
        n = len(profiles)
        self.profile_stats = {
            "sum": {k: v / n for k, v in sums.items()},
            "max": {k: v / n for k, v in maxes.items()},
            "shape": {k: v / n for k, v in shapes.items()},
            "ip": {k: v / n for k, v in ip.items()},
        }

    def compute_score(self, w_freq=0.4, w_delay=0.3, w_pair=0.3):
        f = np.array([self.frequency[n] for n in range(1, 46)], float)
        d = np.array([self.delay[n] for n in range(1, 46)], float)
        pair_aff = np.zeros(45)
        for (a, b), c in self.pair_counts.items():
            pair_aff[a - 1] += c
            pair_aff[b - 1] += c

        def norm(x):
            return np.zeros_like(x) if x.max() == x.min() else (x - x.min()) / (x.max() - x.min())

        fn, dn, pn = norm(f), norm(d), norm(pair_aff)
        for n in range(1, 46):
            self.score[n] = float(w_freq * fn[n - 1] + w_delay * dn[n - 1] + w_pair * pn[n - 1])

    # ---- numar Joker 1..20 ----
    def compute_joker_frequency(self):
        freq = Counter(self.jokers)
        self.j_frequency = {n: freq.get(n, 0) for n in range(1, 21)}

    def compute_joker_delay(self):
        total = len(self.jokers)
        for n in range(1, 21):
            last = None
            for i in range(total - 1, -1, -1):
                if self.jokers[i] == n:
                    last = i
                    break
            self.j_delay[n] = total if last is None else total - 1 - last

    def compute_joker_score(self, w_freq=0.5714, w_delay=0.4286):
        f = np.array([self.j_frequency[n] for n in range(1, 21)], float)
        d = np.array([self.j_delay[n] for n in range(1, 21)], float)

        def norm(x):
            return np.zeros_like(x) if x.max() == x.min() else (x - x.min()) / (x.max() - x.min())

        fn, dn = norm(f), norm(d)
        for n in range(1, 21):
            self.j_score[n] = float(w_freq * fn[n - 1] + w_delay * dn[n - 1])

    def fit(self):
        self.compute_frequency()
        self.compute_delay()
        self.compute_pairs()
        self.compute_transition()
        self.compute_profile_stats()
        self.compute_score()
        self.compute_joker_frequency()
        self.compute_joker_delay()
        self.compute_joker_score()
        return self

    def target_profile(self):
        def top(d):
            return max(d.items(), key=lambda kv: kv[1])[0]
        return {
            "sum": top(self.profile_stats["sum"]),
            "max": top(self.profile_stats["max"]),
            "shape": top(self.profile_stats["shape"]),
            "ip": top(self.profile_stats["ip"]),
        }

    def plausible(self, ticket, target):
        p = draw_profile_joker(ticket)
        ok = 0
        if sum_bucket_joker(p["sum"]) == target["sum"]:
            ok += 1
        if max_zone_joker(p["max"]) == target["max"]:
            ok += 1
        if p["shape"] == target["shape"]:
            ok += 1
        if f'{p["odd"]}/{p["even"]}' == target["ip"]:
            ok += 1
        return ok >= 2

    def gen_main_ticket(self, rng, size=5, pool=12):
        ranked = sorted(self.score.items(), key=lambda x: x[1], reverse=True)[:pool]
        nums = np.array([n for n, _ in ranked])
        w = np.array([s for _, s in ranked], float)
        w = np.ones_like(w) / len(w) if w.sum() == 0 else w / w.sum()
        return sorted(rng.choice(nums, size, replace=False, p=w).tolist())

    def gen_joker_number(self, rng, pool=8):
        ranked = sorted(self.j_score.items(), key=lambda x: x[1], reverse=True)[:pool]
        nums = np.array([n for n, _ in ranked])
        w = np.array([s for _, s in ranked], float)
        w = np.ones_like(w) / len(w) if w.sum() == 0 else w / w.sum()
        return int(rng.choice(nums, 1, replace=False, p=w)[0])

    def gen_tickets(self, rng, count=2, size=5, pool_main=12, pool_joker=8, use_filter=True):
        target = self.target_profile()
        attempts, max_attempts = 0, count * 3000
        ordered = []
        seen = set()
        while len(ordered) < count and attempts < max_attempts:
            main_t = tuple(self.gen_main_ticket(rng, size, pool_main))
            attempts += 1
            if use_filter and not self.plausible(main_t, target):
                continue
            joker_n = self.gen_joker_number(rng, pool_joker)
            key = main_t + (joker_n,)
            if key not in seen:
                seen.add(key)
                ordered.append((main_t, joker_n))
        while len(ordered) < count:
            main_t = tuple(self.gen_main_ticket(rng, size, pool_main))
            joker_n = self.gen_joker_number(rng, pool_joker)
            key = main_t + (joker_n,)
            if key not in seen:
                seen.add(key)
                ordered.append((main_t, joker_n))
        return target, ordered

    def extend_main_to_9(self, rng, ticket5, pool=18, extra=1):
        ranked = sorted(self.score.items(), key=lambda x: x[1], reverse=True)[:pool]
        existing = set(ticket5)
        nums = np.array([n for n, _ in ranked if n not in existing])
        w = np.array([s for n, s in ranked if n not in existing], float)
        if len(nums) < extra:
            rest = [n for n in range(1, 46) if n not in existing and n not in nums.tolist()]
            nums = np.array(nums.tolist() + rest)
            w = np.array(w.tolist() + [0.0] * len(rest))
        w = np.ones_like(w) / len(w) if w.sum() == 0 else w / w.sum()
        extra_nums = rng.choice(nums, extra, replace=False, p=w)
        return sorted(existing.union(extra_nums.tolist()))
