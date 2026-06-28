"""
Loto 6/49 — motor combinat (varianta reproductibila, 12 bilete).

Combina metoda originala (frecventa + intarziere + scoring) cu ideile
metodologice publicate de ponturi.ro:
  - profilul extragerii: suma, numar maxim, I/P (impare/pare), forma
  - matrice de tranzitie pe forma extragerilor consecutive (Markov)
  - perechi de numere frecvente
  - filtru de plauzibilitate a biletului pe baza profilului probabil

REPRODUCTIBILITATE: seed-ul este FIX (649). Pentru acelasi set de date
descarcat, rezultatele sunt MEREU aceleasi. Se schimba doar cand apar
extrageri noi in arhiva.

ONEST: 6/49 e un proces independent si uniform. Metoda NU creste sansa
reala de castig — face doar biletele sa arate realist statistic.
"""

import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIG
# ============================================================

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

    # parametri de generare
    seed: int = 649          # SEED FIX -> reproductibil
    n_bilete: int = 6       # cate bilete vrei
    pool: int = 49           # din cate numere de top alege
    pool_extindere: int = 49 # pool pentru extinderea la 9
    extra_9: int = 3         # cate numere adauga la extinderea 6 -> 9
    v_filter: int = 0 #filtru

# ============================================================
# HTTP + PARSARE
# ============================================================

def build_session():
    session = requests.Session()
    retry = Retry(total=5, connect=5, read=5, backoff_factor=2,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "HEAD"])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.7",
    })
    return session


def valid_draw(numbers):
    return len(numbers) == 6 and len(set(numbers)) == 6 and all(1 <= n <= 49 for n in numbers)


def parse_draws_from_text(text: str):
    pattern = re.compile(
        r"(?<!\d)(\d{4}-\d{2}-\d{2})"
        r"\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})"
        r"\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})(?!\d)"
    )
    rows = []
    for m in pattern.findall(text):
        numbers = list(map(int, m[1:]))
        if valid_draw(numbers):
            rows.append([m[0]] + numbers)
    return rows


def extract_draws_from_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    rows = parse_draws_from_text(soup.get_text("\n", strip=True))
    if not rows:
        rows = parse_draws_from_text(soup.get_text(" ", strip=True))
    return rows


def download_all_loto_data(config: LotoConfig):
    session = build_session()
    all_rows = []
    print("Descarc si parsez arhivele loto...")
    for idx, url in enumerate(config.archive_urls, start=1):
        print(f"[{idx}/{len(config.archive_urls)}] {url}")
        time.sleep(config.sleep_between_requests)
        try:
            response = session.get(url, timeout=config.request_timeout)
            response.raise_for_status()
            rows = extract_draws_from_html(response.text)
        except Exception as e:
            print(f"  -> EROARE: {e}")
            rows = []
        print(f"  -> extrase: {len(rows)}")
        for row in rows:
            all_rows.append({"data": row[0], "n1": row[1], "n2": row[2],
                             "n3": row[3], "n4": row[4], "n5": row[5], "n6": row[6]})

    if not all_rows:
        raise Exception("Nu s-a extras nicio extragere valida (structura HTML schimbata?).")

    df = pd.DataFrame(all_rows)
    df["data_dt"] = pd.to_datetime(df["data"], format="%Y-%m-%d", errors="coerce")
    df = df[df["data_dt"].notna()]
    df = df.drop_duplicates(subset=["data", "n1", "n2", "n3", "n4", "n5", "n6"])
    df = df.sort_values("data_dt").reset_index(drop=True)
    print(f"\nExtrageri unice: {len(df)}")
    return df


# ============================================================
# PROFIL EXTRAGERE
# ============================================================

def draw_profile(numbers):
    nums = sorted(numbers)
    odd = sum(1 for n in nums if n % 2 == 1)
    even = 6 - odd
    s = sum(nums)
    mx = max(nums)
    low = sum(1 for n in nums if n <= 24)
    high = 6 - low
    if high - low >= 2:
        shape = "high-heavy"
    elif low - high >= 2:
        shape = "low-heavy"
    else:
        shape = "balanced"
    return {"sum": s, "max": mx, "odd": odd, "even": even, "shape": shape}


def sum_bucket(s):
    if s < 120:
        return "<120"
    if s <= 149:
        return "120-149"
    if s <= 179:
        return "150-179"
    return ">=180"


def max_zone(mx):
    if mx <= 39:
        return "<=39"
    if mx <= 44:
        return "40-44"
    return "45-49"


# ============================================================
# MOTOR COMBINAT
# ============================================================

class CombinedAnalyzer:
    def __init__(self, df: pd.DataFrame):
        self.df = df.sort_values("data_dt").reset_index(drop=True)
        self.draws = self.df[["n1", "n2", "n3", "n4", "n5", "n6"]].values.tolist()
        self.frequency = {}
        self.delay = {}
        self.score = {}
        self.pair_counts = Counter()
        self.transition = defaultdict(Counter)
        self.profile_stats = {}

    def compute_frequency(self):
        freq = Counter()
        for d in self.draws:
            freq.update(d)
        self.frequency = {n: freq.get(n, 0) for n in range(1, 50)}

    def compute_delay(self):
        total = len(self.draws)
        for n in range(1, 50):
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
        profiles = [draw_profile(d) for d in self.draws]
        for a, b in zip(profiles[:-1], profiles[1:]):
            self.transition[a["shape"]][b["shape"]] += 1

    def compute_profile_stats(self):
        profiles = [draw_profile(d) for d in self.draws]
        sums = Counter(sum_bucket(p["sum"]) for p in profiles)
        maxes = Counter(max_zone(p["max"]) for p in profiles)
        shapes = Counter(p["shape"] for p in profiles)
        ip = Counter(f'{p["odd"]}/{p["even"]}' for p in profiles)
        n = len(profiles)
        self.profile_stats = {
            "sum": {k: v / n for k, v in sums.items()},
            "max": {k: v / n for k, v in maxes.items()},
            "shape": {k: v / n for k, v in shapes.items()},
            "ip": {k: v / n for k, v in ip.items()},
        }

    def compute_score(self, w_freq=0.333, w_delay=0.333, w_pair=0.333):
        f = np.array([self.frequency[n] for n in range(1, 50)], float)
        d = np.array([self.delay[n] for n in range(1, 50)], float)
        pair_aff = np.zeros(49)
        for (a, b), c in self.pair_counts.items():
            pair_aff[a - 1] += c
            pair_aff[b - 1] += c

        def norm(x):
            return np.zeros_like(x) if x.max() == x.min() else (x - x.min()) / (x.max() - x.min())

        fn, dn, pn = norm(f), norm(d), norm(pair_aff)
        for n in range(1, 50):
            self.score[n] = w_freq * fn[n - 1] + w_delay * dn[n - 1] + w_pair * pn[n - 1]

    def fit(self):
        self.compute_frequency()
        self.compute_delay()
        self.compute_pairs()
        self.compute_transition()
        self.compute_profile_stats()
        self.compute_score()
        return self

    def _target_profile(self):
        def top(d):
            return max(d.items(), key=lambda kv: kv[1])[0]
        return {
            "sum": top(self.profile_stats["sum"]),
            "max": top(self.profile_stats["max"]),
            "shape": top(self.profile_stats["shape"]),
            "ip": top(self.profile_stats["ip"]),
        }

    def _plausible(self, ticket, target):
        p = draw_profile(ticket)
        ok = 0
        if sum_bucket(p["sum"]) == target["sum"]:
            ok += 1
        if max_zone(p["max"]) == target["max"]:
            ok += 1
        if p["shape"] == target["shape"]:
            ok += 1
        if f'{p["odd"]}/{p["even"]}' == target["ip"]:
            ok += 1
        return ok >= 2

    def generate_ticket(self, size=6, pool=20):
        ranked = sorted(self.score.items(), key=lambda x: x[1], reverse=True)[:pool]
        nums = np.array([n for n, _ in ranked])
        w = np.array([s for _, s in ranked], float)
        w = np.ones_like(w) / len(w) if w.sum() == 0 else w / w.sum()
        return sorted(np.random.choice(nums, size, replace=False, p=w).tolist())

    def generate_tickets(self, count=12, size=6, pool=20, use_profile_filter=True):
        target = self._target_profile()
        attempts, max_attempts =  0, count * 2000
        # ordonez ca lista (nu set) ca sa pastrez ordinea de generare -> reproductibil
        ordered = []
        seen = set()
        while len(ordered) < count and attempts < max_attempts:
            t = tuple(self.generate_ticket(size, pool))
            attempts += 1
            if use_profile_filter and not self._plausible(t, target):
                continue
            if t not in seen:
                seen.add(t)
                ordered.append(t)
        while len(ordered) < count:
            t = tuple(self.generate_ticket(size, pool))
            if t not in seen:
                seen.add(t)
                ordered.append(t)
        return target, [list(t) for t in ordered]

    def extend_to_9(self, ticket_6, pool=22, extra=3):
        ranked = sorted(self.score.items(), key=lambda x: x[1], reverse=True)[:pool]
        existing = set(ticket_6)
        nums = np.array([n for n, _ in ranked if n not in existing])
        w = np.array([s for n, s in ranked if n not in existing], float)
        w = np.ones_like(w) / len(w) if w.sum() == 0 else w / w.sum()
        extra_nums = np.random.choice(nums, extra, replace=False, p=w)
        return sorted(existing.union(extra_nums.tolist()))

    def report(self):
        print("\n=== TOP 12 NUMERE (scor combinat) ===")
        for n, s in sorted(self.score.items(), key=lambda x: -x[1])[:12]:
            print(f"{n:2d} | scor={s:.4f} | freq={self.frequency[n]} | delay={self.delay[n]}")

        print("\n=== TOP 8 PERECHI ===")
        for (a, b), c in self.pair_counts.most_common(8):
            print(f"{a:2d}-{b:2d} | aparitii={c}")

        print("\n=== PROFIL PROBABIL ===")
        t = self._target_profile()
        print(f"Suma probabila:    {t['sum']}  ({self.profile_stats['sum'][t['sum']]*100:.1f}%)")
        print(f"Zona maxim:        {t['max']}  ({self.profile_stats['max'][t['max']]*100:.1f}%)")
        print(f"Forma:             {t['shape']}  ({self.profile_stats['shape'][t['shape']]*100:.1f}%)")
        print(f"I/P (impare/pare): {t['ip']}  ({self.profile_stats['ip'][t['ip']]*100:.1f}%)")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    config = LotoConfig()

    # SEED FIX -> rezultate identice la fiecare rulare pentru acelasi set de date
    np.random.seed(config.seed)

    df = download_all_loto_data(config)
    df.to_csv("loto_extrageri.csv", index=False)

    analyzer = CombinedAnalyzer(df).fit()
    analyzer.report()

    print(f"\n=== {config.n_bilete} BILETE 6 NUMERE (seed fix {config.seed}) ===")
    target, tickets_6 = analyzer.generate_tickets(
        count=config.n_bilete, size=6, pool=config.pool,use_profile_filter=config.v_filter)
    for i, t in enumerate(tickets_6, 1):
        p = draw_profile(t)
        print(f"Bilet {i:2d}: {t}  | suma={p['sum']} I/P={p['odd']}/{p['even']} forma={p['shape']}")

    print("\n=== EXTINDERE LA 9 NUMERE ===")
    for i, t6 in enumerate(tickets_6, 1):
        t9 = analyzer.extend_to_9(t6, pool=config.pool_extindere, extra=config.extra_9)
        print(f"Bilet 9/{i:2d}: {t9}")