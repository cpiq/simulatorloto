import re
import time
import random
from dataclasses import dataclass
from itertools import combinations
from collections import Counter
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import requests
import truststore
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

truststore.inject_into_ssl()


@dataclass
class LotoConfig:
    archive_urls: Tuple[str, ...] = (
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
    random_seed: int = 649

    recent_window: int = 150
    pool_size: int = 24
    ticket_count: int = 12

    w_freq: float = 0.35
    w_recent: float = 0.30
    w_delay: float = 0.20
    w_high: float = 0.1
    w_pair: float = 0.07
    w_triple: float = 0.03

    temperature: float = 0.80
    max_attempts_per_ticket: int = 40000

    min_sum_6: int = 90
    max_sum_6: int = 190
    min_even_6: int = 2
    max_even_6: int = 4
    min_low_6: int = 2
    max_low_6: int = 4
    max_consecutive_run_6: int = 3
    max_same_last_digit_6: int = 6
    max_common_with_history_6: int = 4
    min_mean_score_6: float = 0.50

    backtest_last_n_draws: int = 30
    montecarlo_group_iterations: int = 300000
    montecarlo_keep_best: int = 80


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
        }
    )
    return session


def valid_draw(numbers: List[int]) -> bool:
    return len(numbers) == 6 and len(set(numbers)) == 6 and all(1 <= n <= 49 for n in numbers)


def parse_draws_from_text(text: str) -> List[Tuple[str, List[int]]]:
    pattern = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})\s+((?:\d{1,2}\s+){5}\d{1,2})(?!\d)")
    rows = []
    for m in pattern.finditer(text):
        draw_date = m.group(1)
        numbers = list(map(int, m.group(2).split()))
        if valid_draw(numbers):
            rows.append((draw_date, sorted(numbers)))
    return rows


def extract_draws_from_html(html: str) -> List[Tuple[str, List[int]]]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    rows = parse_draws_from_text(text)
    if rows:
        return rows
    compact = soup.get_text("\n", strip=True)
    return parse_draws_from_text(compact)


def download_all_draws(config: LotoConfig) -> pd.DataFrame:
    session = build_session()
    all_rows = []

    print("Descarc si parsez arhivele loto...")
    for i, url in enumerate(config.archive_urls, 1):
        print(f"[{i}/{len(config.archive_urls)}] {url}")
        time.sleep(config.sleep_between_requests)
        r = session.get(url, timeout=config.request_timeout)
        r.raise_for_status()
        rows = extract_draws_from_html(r.text)
        print(f"   -> extrase {len(rows)} extrageri")
        for draw_date, nums in rows:
            all_rows.append(
                {
                    "data": draw_date,
                    "n1": nums[0],
                    "n2": nums[1],
                    "n3": nums[2],
                    "n4": nums[3],
                    "n5": nums[4],
                    "n6": nums[5],
                    "source": url,
                }
            )

    if not all_rows:
        raise RuntimeError("Nu s-a extras nicio extragere valida.")

    df = pd.DataFrame(all_rows)
    df["data_dt"] = pd.to_datetime(df["data"], format="%Y-%m-%d", errors="coerce")
    df = df[df["data_dt"].notna()].copy()
    df = df.sort_values(["data_dt", "n1", "n2", "n3", "n4", "n5", "n6"]).reset_index(drop=True)
    df = df.drop_duplicates(subset=["data", "n1", "n2", "n3", "n4", "n5", "n6"], keep="first")
    return df.reset_index(drop=True)


class LotoAnalyzer:
    def __init__(self, df: pd.DataFrame, config: LotoConfig):
        self.df = df.copy()
        self.config = config
        self.draws = [tuple(sorted(x)) for x in self.df[["n1", "n2", "n3", "n4", "n5", "n6"]].values.tolist()]
        self.total_draws = len(self.draws)

        self.frequency: Dict[int, int] = {}
        self.recent_frequency: Dict[int, int] = {}
        self.delay: Dict[int, int] = {}
        self.score: Dict[int, float] = {}

        self.pair_freq = Counter()
        self.triple_freq = Counter()
        self.group6_freq = Counter()

    @staticmethod
    def normalize(arr: np.ndarray) -> np.ndarray:
        arr = np.array(arr, dtype=float)
        amin, amax = arr.min(), arr.max()
        if amax == amin:
            return np.zeros_like(arr, dtype=float)
        return (arr - amin) / (amax - amin)

    def compute_frequency(self):
        freq = Counter()
        for draw in self.draws:
            freq.update(draw)
        self.frequency = {n: freq.get(n, 0) for n in range(1, 50)}

    def compute_recent_frequency(self):
        window = self.config.recent_window
        recent_draws = self.draws[-window:] if len(self.draws) > window else self.draws
        freq = Counter()
        for draw in recent_draws:
            freq.update(draw)
        self.recent_frequency = {n: freq.get(n, 0) for n in range(1, 50)}

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

    def compute_pair_triple_stats(self):
        self.pair_freq = Counter()
        self.triple_freq = Counter()
        self.group6_freq = Counter()
        for draw in self.draws:
            draw = tuple(sorted(draw))
            self.group6_freq[draw] += 1
            for pair in combinations(draw, 2):
                self.pair_freq[tuple(sorted(pair))] += 1
            for triple in combinations(draw, 3):
                self.triple_freq[tuple(sorted(triple))] += 1

    def compute_scores(self):
        self.compute_frequency()
        self.compute_recent_frequency()
        self.compute_delay()
        self.compute_pair_triple_stats()

        f = np.array([self.frequency[n] for n in range(1, 50)], dtype=float)
        r = np.array([self.recent_frequency[n] for n in range(1, 50)], dtype=float)
        d = np.array([self.delay[n] for n in range(1, 50)], dtype=float)
        h = np.array([n for n in range(1, 50)], dtype=float)

        fn = self.normalize(f)
        rn = self.normalize(r)
        dn = self.normalize(d)
        hn = self.normalize(h)

        for i, n in enumerate(range(1, 50)):
            self.score[n] = (
                self.config.w_freq * fn[i]
                + self.config.w_recent * rn[i]
                + self.config.w_delay * dn[i]
                + self.config.w_high * hn[i]
            )

    @staticmethod
    def count_even(group):
        return sum(1 for n in group if n % 2 == 0)

    @staticmethod
    def count_low(group, split_at=24):
        return sum(1 for n in group if n <= split_at)

    @staticmethod
    def max_consecutive_run(group):
        nums = sorted(group)
        best = 1
        cur = 1
        for i in range(1, len(nums)):
            if nums[i] == nums[i - 1] + 1:
                cur += 1
                best = max(best, cur)
            else:
                cur = 1
        return best

    @staticmethod
    def max_same_last_digit(group):
        c = Counter([n % 10 for n in group])
        return max(c.values())

    def shares_too_much_with_history(self, group, max_common=4):
        s = set(group)
        for draw in self.draws:
            if len(s.intersection(draw)) > max_common:
                return True
        return False

    def mean_individual_score(self, group):
        return float(np.mean([self.score[n] for n in group]))

    def structural_bonus(self, group):
        s = sum(group)
        odd = sum(1 for n in group if n % 2 == 1)
        high = sum(1 for n in group if n >= 30)
        consecutive_pairs = sum(1 for n in group if n + 1 in set(group))

        bonus = 0.0
        if 120 <= s <= 210:
            bonus += 0.08
        if odd in (2, 3, 4):
            bonus += 0.06
        if high >= 2:
            bonus += 0.05
        if consecutive_pairs == 1:
            bonus += 0.03
        if s < 90:
            bonus -= 0.10
        return bonus

    def group_score(self, group):
        group = tuple(sorted(group))
        individual = float(np.mean([self.score[n] for n in group]))

        pair_values = [self.pair_freq.get(tuple(sorted(p)), 0) for p in combinations(group, 2)]
        triple_values = [self.triple_freq.get(tuple(sorted(t)), 0) for t in combinations(group, 3)]
        delay_values = [self.delay[n] for n in group]

        pair_component = float(np.mean(pair_values)) if pair_values else 0.0
        triple_component = float(np.mean(triple_values)) if triple_values else 0.0
        delay_component = float(np.mean(delay_values)) if delay_values else 0.0

        max_pair = max(self.pair_freq.values()) if self.pair_freq else 1
        max_triple = max(self.triple_freq.values()) if self.triple_freq else 1
        max_delay = max(self.delay.values()) if self.delay else 1

        pair_norm = pair_component / max_pair if max_pair else 0.0
        triple_norm = triple_component / max_triple if max_triple else 0.0
        delay_norm = delay_component / max_delay if max_delay else 0.0

        score = (
            0.70 * individual
            + self.config.w_pair * pair_norm
            + self.config.w_triple * triple_norm
            + 0.10 * delay_norm
            + 0.10 * self.structural_bonus(group)
        )
        return score

    def valid_group_6(self, group):
        group = tuple(sorted(group))
        s = sum(group)
        even = self.count_even(group)
        low = self.count_low(group)
        run = self.max_consecutive_run(group)
        same_last = self.max_same_last_digit(group)
        mean_score = self.mean_individual_score(group)

        if not (self.config.min_sum_6 <= s <= self.config.max_sum_6):
            return False
        if not (self.config.min_even_6 <= even <= self.config.max_even_6):
            return False
        if not (self.config.min_low_6 <= low <= self.config.max_low_6):
            return False
        if run > self.config.max_consecutive_run_6:
            return False
        if same_last > self.config.max_same_last_digit_6:
            return False
        if self.shares_too_much_with_history(group, self.config.max_common_with_history_6):
            return False
        if mean_score < self.config.min_mean_score_6:
            return False
        return True

    def get_weighted_pool(self):
        ranked = sorted(self.score.items(), key=lambda x: x[1], reverse=True)[: self.config.pool_size]
        nums = np.array([n for n, _ in ranked], dtype=int)
        weights = np.array([s for _, s in ranked], dtype=float)

        if weights.sum() == 0:
            weights = np.ones_like(weights, dtype=float) / len(weights)
        else:
            weights = weights / weights.sum()

        if self.config.temperature != 1.0:
            weights = np.power(weights, 1.0 / self.config.temperature)
            weights = weights / weights.sum()

        return nums, weights

    def generate_ticket_6(self):
        nums, weights = self.get_weighted_pool()

        for _ in range(self.config.max_attempts_per_ticket):
            group = tuple(sorted(np.random.choice(nums, size=6, replace=False, p=weights)))
            if self.valid_group_6(group):
                return list(map(int, group))

        raise RuntimeError("Nu am gasit nicio varianta valida de 6.")

    def generate_tickets_6(self):
        tickets = set()
        total_limit = self.config.ticket_count * self.config.max_attempts_per_ticket * 2
        attempts = 0

        while len(tickets) < self.config.ticket_count and attempts < total_limit:
            t = tuple(self.generate_ticket_6())
            tickets.add(t)
            attempts += 1

        return [list(t) for t in sorted(tickets)]

    def montecarlo_best_groups(self):
        nums, weights = self.get_weighted_pool()
        best = {}

        for _ in range(self.config.montecarlo_group_iterations):
            group = tuple(sorted(np.random.choice(nums, size=6, replace=False, p=weights)))
            if not self.valid_group_6(group):
                continue
            sc = self.group_score(group)
            if group not in best or sc > best[group]:
                best[group] = sc

        rows = [{"numere": " ".join(map(str, k)), "score_grup": v} for k, v in best.items()]
        if not rows:
            return pd.DataFrame(columns=["numere", "score_grup"])

        df = pd.DataFrame(rows).sort_values("score_grup", ascending=False).head(self.config.montecarlo_keep_best)
        return df.reset_index(drop=True)

    def backtest_last_draws(self):
        n = self.config.backtest_last_n_draws
        if len(self.df) <= n + 30:
            raise RuntimeError("Prea putine extrageri pentru backtesting.")

        results = []
        source_df = self.df.copy().reset_index(drop=True)

        for idx in range(len(source_df) - n, len(source_df)):
            train_df = source_df.iloc[:idx].copy()
            real_draw = sorted(source_df.iloc[idx][["n1", "n2", "n3", "n4", "n5", "n6"]].astype(int).tolist())

            analyzer = LotoAnalyzer(train_df, self.config)
            analyzer.compute_scores()
            tickets = analyzer.generate_tickets_6()

            best_match = 0
            best_ticket = None
            real_set = set(real_draw)

            for ticket in tickets:
                match_count = len(set(ticket).intersection(real_set))
                if match_count > best_match:
                    best_match = match_count
                    best_ticket = ticket

            results.append(
                {
                    "data": source_df.iloc[idx]["data"],
                    "real_draw": " ".join(map(str, real_draw)),
                    "best_ticket": " ".join(map(str, best_ticket)) if best_ticket else "",
                    "best_match": best_match,
                }
            )

        return pd.DataFrame(results)


def main():
    config = LotoConfig()

    np.random.seed(config.random_seed)
    random.seed(config.random_seed)

    df = download_all_draws(config)
    print(f"\nTotal extrageri unice: {len(df)}")

    analyzer = LotoAnalyzer(df, config)
    analyzer.compute_scores()

    ranking_numbers = pd.DataFrame(
        [
            {
                "numar": n,
                "frecventa": analyzer.frequency[n],
                "frecventa_recenta": analyzer.recent_frequency[n],
                "intarziere": analyzer.delay[n],
                "scor": round(analyzer.score[n], 6),
            }
            for n in range(1, 50)
        ]
    ).sort_values(["scor", "frecventa_recenta", "frecventa", "intarziere", "numar"],
                  ascending=[False, False, False, False, True])

    print("\nTOP 15 NUMERE")
    print(ranking_numbers.head(15).to_string(index=False))

    print("\nTOP GRUPURI MONTE CARLO")
    best_groups = analyzer.montecarlo_best_groups()
    print(best_groups.head(10).to_string(index=False))

    print("\nBILETE GENERATE")
    tickets = analyzer.generate_tickets_6()
    for i, t in enumerate(tickets, 1):
        print(f"Varianta {i}: {t} | suma={sum(t)} | scor_med={np.mean([analyzer.score[x] for x in t]):.4f}")

    print("\nBACKTESTING")
    bt = analyzer.backtest_last_draws()
    print(bt.to_string(index=False))

    dist = bt["best_match"].value_counts().sort_index()
    print("\nDISTRIBUTIE MATCH-URI (cel mai bun bilet din setul generat)")
    for k, v in dist.items():
        print(f"{k} potriviri: {v}")

    print(f"\nMedia best_match: {bt['best_match'].mean():.3f}")


if __name__ == "__main__":
    main()
