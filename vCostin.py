import re
import time
from collections import Counter
from dataclasses import dataclass

import numpy as np
import pandas as pd
import requests
import truststore
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

truststore.inject_into_ssl()


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


# ============================================================
# HTTP
# ============================================================

def build_session():
    session = requests.Session()

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"]
    )

    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })

    return session


# ============================================================
# PARSARE
# ============================================================

def valid_draw(numbers):
    return (
        len(numbers) == 6
        and len(set(numbers)) == 6
        and all(1 <= n <= 49 for n in numbers)
    )


def parse_draws_from_text(text: str):
    pattern = re.compile(
        r"(?<!\d)"
        r"(\d{4}-\d{2}-\d{2})"
        r"\s+(\d{1,2})"
        r"\s+(\d{1,2})"
        r"\s+(\d{1,2})"
        r"\s+(\d{1,2})"
        r"\s+(\d{1,2})"
        r"\s+(\d{1,2})"
        r"(?!\d)"
    )

    rows = []
    for m in pattern.findall(text):
        draw_date = m[0]
        numbers = list(map(int, m[1:]))

        if valid_draw(numbers):
            rows.append([draw_date] + numbers)

    return rows


def extract_draws_from_html(html: str):
    soup = BeautifulSoup(html, "html.parser")

    text = soup.get_text("\n", strip=True)
    rows = parse_draws_from_text(text)

    if not rows:
        compact_text = soup.get_text(" ", strip=True)
        rows = parse_draws_from_text(compact_text)

    return rows


def download_all_loto_data(config: LotoConfig):
    session = build_session()
    all_rows = []
    stats = []

    print("Descarc si parsez arhivele loto...")

    for idx, url in enumerate(config.archive_urls, start=1):
        print(f"[{idx}/{len(config.archive_urls)}] {url}")
        time.sleep(config.sleep_between_requests)

        response = session.get(url, timeout=config.request_timeout)
        response.raise_for_status()

        rows = extract_draws_from_html(response.text)
        print(f"  -> extrase: {len(rows)}")

        stats.append((url, len(rows)))

        for row in rows:
            all_rows.append({
                "data": row[0],
                "n1": row[1],
                "n2": row[2],
                "n3": row[3],
                "n4": row[4],
                "n5": row[5],
                "n6": row[6],
                "source_url": url
            })

    if not all_rows:
        raise Exception(
            "Nu s-a putut extrage nicio extragere valida. "
            "Cel mai probabil site-ul a schimbat structura HTML."
        )

    df = pd.DataFrame(all_rows)

    df["data_dt"] = pd.to_datetime(df["data"], format="%Y-%m-%d", errors="coerce")
    df = df[df["data_dt"].notna()].copy()

    df = df.sort_values(
        ["data_dt", "n1", "n2", "n3", "n4", "n5", "n6", "source_url"]
    ).reset_index(drop=True)

    duplicate_mask = df.duplicated(
        subset=["data", "n1", "n2", "n3", "n4", "n5", "n6"],
        keep=False
    )
    anomalies_df = df.loc[duplicate_mask, [
        "data", "n1", "n2", "n3", "n4", "n5", "n6", "source_url"
    ]].copy()

    df_final = df.drop_duplicates(
        subset=["data", "n1", "n2", "n3", "n4", "n5", "n6"],
        keep="first"
    ).copy()

    df_final = df_final.sort_values(
        ["data_dt", "n1", "n2", "n3", "n4", "n5", "n6"]
    ).reset_index(drop=True)

    print("\n=== STATISTICA PE SURSE ===")
    for url, cnt in stats:
        print(f"{cnt:4d} | {url}")

    print(f"\nExtrageri unice in memorie: {len(df_final)}")
    print(f"Anomalii detectate in memorie: {len(anomalies_df)}")

    return df_final, anomalies_df


# ============================================================
# ANALIZATOR
# ============================================================

class LotoAnalyzer:
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self.df["data_dt"] = pd.to_datetime(self.df["data"], format="%Y-%m-%d", errors="coerce")
        self.df = self.df[self.df["data_dt"].notna()].copy()
        self.df = self.df.sort_values("data_dt").reset_index(drop=True)

        self.draws = self.df[["n1", "n2", "n3", "n4", "n5", "n6"]].values.tolist()
        self.frequency = {}
        self.delay = {}
        self.score = {}

    def compute_frequency(self):
        freq = Counter()
        for draw in self.draws:
            freq.update(draw)
        self.frequency = {n: freq.get(n, 0) for n in range(1, 50)}

    def compute_delay(self):
        total = len(self.draws)
        delay = {}

        for n in range(1, 50):
            last = None
            for i in range(total - 1, -1, -1):
                if n in self.draws[i]:
                    last = i
                    break
            delay[n] = total if last is None else total - 1 - last

        self.delay = delay

    def compute_score(self, w_freq=0.6, w_delay=0.4):
        f = np.array([self.frequency.get(n, 0) for n in range(1, 50)], dtype=float)
        d = np.array([self.delay.get(n, 0) for n in range(1, 50)], dtype=float)

        fmin, fmax = f.min(), f.max()
        dmin, dmax = d.min(), d.max()

        self.score = {}
        for n in range(1, 50):
            fn = 0 if fmax == fmin else (self.frequency[n] - fmin) / (fmax - fmin)
            dn = 0 if dmax == dmin else (self.delay[n] - dmin) / (dmax - dmin)
            self.score[n] = w_freq * fn + w_delay * dn

    def get_ranking_dataframe(self):
        rows = []
        for n in range(1, 50):
            rows.append({
                "numar": n,
                "frecventa": self.frequency.get(n, 0),
                "intarziere": self.delay.get(n, 0),
                "scor": round(self.score.get(n, 0), 6)
            })

        ranking_df = pd.DataFrame(rows)
        ranking_df = ranking_df.sort_values(
            by=["scor", "frecventa", "intarziere", "numar"],
            ascending=[False, False, False, True]
        ).reset_index(drop=True)
        return ranking_df

    def top_numbers(self, k=10):
        return sorted(self.score.items(), key=lambda x: x[1], reverse=True)[:k]

    def generate_ticket(self, size=6, pool=20):
        if size > pool:
            raise ValueError("size nu poate fi mai mare decat pool")

        ranked = sorted(self.score.items(), key=lambda x: x[1], reverse=True)
        candidates = ranked[:pool]

        nums = np.array([n for n, _ in candidates])
        weights = np.array([s for _, s in candidates], dtype=float)

        if weights.sum() == 0:
            weights = np.ones_like(weights, dtype=float) / len(weights)
        else:
            weights = weights / weights.sum()

        ticket = np.random.choice(nums, size, replace=False, p=weights)
        return sorted(ticket.tolist())

    def generate_tickets(self, count=5, size=6, pool=20):
        tickets = set()
        max_attempts = count * 500
        attempts = 0

        while len(tickets) < count and attempts < max_attempts:
            t = tuple(self.generate_ticket(size=size, pool=pool))
            tickets.add(t)
            attempts += 1

        return [list(t) for t in sorted(tickets)]

    def generate_ticket_9_from_ticket_6(self, ticket_6, pool=20, extra=3):
        if extra <= 0:
            return sorted(ticket_6)

        ranked = sorted(self.score.items(), key=lambda x: x[1], reverse=True)
        candidates = ranked[:pool]

        existing = set(ticket_6)

        remaining_nums = np.array([n for n, _ in candidates if n not in existing])
        remaining_weights = np.array([s for n, s in candidates if n not in existing], dtype=float)

        if len(remaining_nums) < extra:
            raise ValueError("Nu exista suficiente numere ramase pentru a extinde varianta de 6 la 9")

        if remaining_weights.sum() == 0:
            remaining_weights = np.ones_like(remaining_weights, dtype=float) / len(remaining_weights)
        else:
            remaining_weights = remaining_weights / remaining_weights.sum()

        extra_nums = np.random.choice(remaining_nums, extra, replace=False, p=remaining_weights)
        ticket_9 = sorted(list(existing.union(extra_nums.tolist())))
        return ticket_9

    def report(self):
        print("\n=== TOP NUMERE ===")
        for n, s in self.top_numbers(10):
            print(f"{n:2d} | scor={s:.4f} | freq={self.frequency[n]} | delay={self.delay[n]}")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    config = LotoConfig()
    np.random.seed(649)

    df_draws, df_anomalies = download_all_loto_data(config)

    analyzer = LotoAnalyzer(df_draws)
    analyzer.compute_frequency()
    analyzer.compute_delay()
    analyzer.compute_score()

    analyzer.report()

    ranking_df = analyzer.get_ranking_dataframe()
    print("\n=== TOP 15 RANKING ===")
    print(ranking_df.head(15).to_string(index=False))

    if not df_anomalies.empty:
        print("\n=== ANOMALII DETECTATE ===")
        print(df_anomalies.head(20).to_string(index=False))

    print("\n=== VARIANTE 6 NUMERE ===")
    tickets_6 = analyzer.generate_tickets(count=3, size=6, pool=20)
    for i, t in enumerate(tickets_6, 1):
        print(f"Varianta 6/{i}: {t}")

    print("\n=== VARIANTE 9 NUMERE ===")
    tickets_9 = []
    for i, t6 in enumerate(tickets_6, 1):
        t9 = analyzer.generate_ticket_9_from_ticket_6(ticket_6=t6, pool=20, extra=3)
        tickets_9.append(t9)
        print(f"Varianta 9/{i}: {t9}")
