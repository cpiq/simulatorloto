#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FOOTBALL BETTING ANALYZER v5.4
- Interval: urmatoarele N zile (default 7)
- Data sources:
    - football-data.org: fixtures, standings, team matches, head2head
    - TheSportsDB: optional enrichment pentru echipe
- Model: Poisson + time-weighting + forma + standings + H2H
- Adaugat: ticket_model per meci (safe / balanced / aggressive)
"""

import os
import sys
import json
import time
import math
import argparse
from datetime import datetime, timedelta, timezone

import requests
import numpy as np
from scipy.stats import poisson
from tabulate import tabulate
from colorama import Fore, Style, init

init(autoreset=True)

FD_BASE = "https://api.football-data.org/v4"
FD_KEY = 'a8509d5a6f6a44b69db1f9ade8d67b99'
TSDB_BASE = "https://www.thesportsdb.com/api/v1/json/3"
DEFAULT_COMPETITIONS =["PL", "PD", "SA", "BL1", "FL1", "CL","EC","PPL","ELC","DED","WC"]

FD_HEADERS = {
    "X-Auth-Token": FD_KEY or "",
    "User-Agent": "football-betting-analyzer/5.4",
}

FD_SESSION = requests.Session()
FD_SESSION.headers.update(FD_HEADERS)

TSDB_SESSION = requests.Session()
TSDB_SESSION.headers.update({"User-Agent": "football-betting-analyzer/5.4"})


def header(text):
    print()
    print(Fore.CYAN + "=" * 80)
    print(Fore.CYAN + f"  {text}")
    print(Fore.CYAN + "=" * 80)


def section(text):
    print(Fore.YELLOW + f"\n  >> {text}")


def ok(text):
    print(Fore.GREEN + f"     [OK] {text}")


def warn(text):
    print(Fore.YELLOW + f"     [!]  {text}")


def err(text):
    print(Fore.RED + f"     [ERR] {text}")


def ensure_fd_key():
    if not FD_KEY:
        raise RuntimeError("Lipseste FOOTBALL_DATA_KEY. Scriptul are nevoie de football-data.org.")


def week_range_from(date_str, days=7):
    start = datetime.strptime(date_str, "%Y-%m-%d").date()
    end = start + timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def fd_get(path, params=None, timeout=30):
    url = f"{FD_BASE}{path}"
    for attempt in range(3):
        try:
            r = FD_SESSION.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", "5"))
                warn(f"Rate limit football-data.org, astept {retry_after}s...")
                time.sleep(retry_after)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"Eroare football-data.org {url}: {e}")
            time.sleep(2 * (attempt + 1))


def tsdb_get(path, params=None, timeout=20):
    url = f"{TSDB_BASE}{path}"
    for attempt in range(2):
        try:
            r = TSDB_SESSION.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 1:
                return None
            time.sleep(1)


def normalize_name(name):
    if not name:
        return ""
    s = name.strip().lower()
    replacements = {
        "fc ": "",
        " cf": "",
        " c.f.": "",
        " afc": "",
        "ssc ": "",
        "sc ": "",
        "ac ": "",
        "as ": "",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    return " ".join(s.split())


class FootballDataFetcher:
    def __init__(self):
        self.team_matches_cache = {}
        self.standings_cache = {}
        self.match_h2h_cache = {}

    def get_week_matches(self, competition_codes, start_date_str, days=7):
        date_from, date_to = week_range_from(start_date_str, days)
        data = fd_get("/matches", params={"dateFrom": date_from, "dateTo": date_to})
        matches = []
        for m in data.get("matches", []):
            comp = m.get("competition", {})
            code = comp.get("code")
            if code not in competition_codes:
                continue
            home = m.get("homeTeam", {})
            away = m.get("awayTeam", {})
            if not home or not away:
                continue
            matches.append({
                "match_id": m.get("id"),
                "competition_code": code,
                "league": comp.get("name", code),
                "home_team": home.get("name"),
                "away_team": away.get("name"),
                "home_team_id": home.get("id"),
                "away_team_id": away.get("id"),
                "status": m.get("status"),
                "utcDate": m.get("utcDate"),
                "matchday": m.get("matchday"),
            })
        return matches

    def get_team_matches(self, team_id, limit=30, lookback_days=730):
        cache_key = (team_id, limit, lookback_days)
        if cache_key in self.team_matches_cache:
            return self.team_matches_cache[cache_key]

        date_to = datetime.now(timezone.utc).date()
        date_from = date_to - timedelta(days=lookback_days)

        attempts = [
            {"status": "FINISHED", "dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat(), "limit": limit},
            {"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat(), "limit": limit},
            {"status": "FINISHED", "limit": limit},
            {"limit": limit},
        ]

        last_error = None
        data = None

        for params in attempts:
            try:
                data = fd_get(f"/teams/{team_id}/matches", params=params)
                if data is not None:
                    break
            except Exception as e:
                last_error = e
                continue

        if data is None:
            raise RuntimeError(f"Eroare football-data.org /teams/{team_id}/matches: {last_error}")

        out = []
        for m in data.get("matches", []):
            score = m.get("score", {}).get("fullTime", {})
            hg = score.get("home")
            ag = score.get("away")
            if hg is None or ag is None:
                continue

            dt = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
            home = m.get("homeTeam", {})
            away = m.get("awayTeam", {})

            out.append({
                "date": dt,
                "match_id": m.get("id"),
                "competition_code": m.get("competition", {}).get("code"),
                "home_team": home.get("name"),
                "away_team": away.get("name"),
                "home_team_id": home.get("id"),
                "away_team_id": away.get("id"),
                "home_goals": int(hg),
                "away_goals": int(ag),
                "result": "H" if hg > ag else ("A" if ag > hg else "D"),
            })

        out = sorted(out, key=lambda x: x["date"])[-limit:]
        self.team_matches_cache[cache_key] = out
        return out

    def get_match_head2head(self, match_id, limit=10):
        if (match_id, limit) in self.match_h2h_cache:
            return self.match_h2h_cache[(match_id, limit)]
        data = fd_get(f"/matches/{match_id}/head2head", params={"limit": limit})
        out = []
        for m in data.get("matches", []):
            score = m.get("score", {}).get("fullTime", {})
            hg = score.get("home")
            ag = score.get("away")
            if hg is None or ag is None:
                continue
            dt = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
            home = m.get("homeTeam", {})
            away = m.get("awayTeam", {})
            out.append({
                "date": dt,
                "match_id": m.get("id"),
                "home_team": home.get("name"),
                "away_team": away.get("name"),
                "home_team_id": home.get("id"),
                "away_team_id": away.get("id"),
                "home_goals": int(hg),
                "away_goals": int(ag),
                "result": "H" if hg > ag else ("A" if ag > hg else "D"),
            })
        self.match_h2h_cache[(match_id, limit)] = out
        return out

    def get_competition_standings(self, code):
        if code in self.standings_cache:
            return self.standings_cache[code]
        data = fd_get(f"/competitions/{code}/standings")
        tables = data.get("standings", [])
        total_table = next((t for t in tables if t.get("type") == "TOTAL"), None)
        result = {}
        if total_table:
            for row in total_table.get("table", []):
                team = row.get("team", {})
                tid = team.get("id")
                if tid:
                    result[tid] = {
                        "position": row.get("position"),
                        "points": row.get("points"),
                        "goalDifference": row.get("goalDifference", 0),
                        "won": row.get("won", 0),
                        "draw": row.get("draw", 0),
                        "lost": row.get("lost", 0),
                    }
        self.standings_cache[code] = result
        return result


class TheSportsDBEnricher:
    def __init__(self):
        self.team_lookup_cache = {}

    def search_team(self, team_name):
        key = normalize_name(team_name)
        if key in self.team_lookup_cache:
            return self.team_lookup_cache[key]
        data = tsdb_get("/searchteams.php", params={"t": team_name})
        teams = (data or {}).get("teams") or []
        best = None
        for t in teams:
            candidate = t.get("strTeam")
            if normalize_name(candidate) == key:
                best = t
                break
        if not best and teams:
            best = teams[0]
        self.team_lookup_cache[key] = best
        return best

    def enrich_match(self, home_team, away_team):
        home = self.search_team(home_team)
        away = self.search_team(away_team)
        return {
            "home_tsdb": {
                "league": (home or {}).get("strLeague"),
                "country": (home or {}).get("strCountry"),
                "stadium": (home or {}).get("strStadium"),
                "founded": (home or {}).get("intFormedYear"),
                "badge": (home or {}).get("strBadge"),
            } if home else None,
            "away_tsdb": {
                "league": (away or {}).get("strLeague"),
                "country": (away or {}).get("strCountry"),
                "stadium": (away or {}).get("strStadium"),
                "founded": (away or {}).get("intFormedYear"),
                "badge": (away or {}).get("strBadge"),
            } if away else None,
        }


class PoissonModel:
    @staticmethod
    def time_weight(dates, xi=0.012):
        if not dates:
            return np.ones(1)
        latest = max(dates)
        return np.array([math.exp(-xi * ((latest - d).days / 3.5)) for d in dates])

    @staticmethod
    def fit(matches):
        if not matches or len(matches) < 6:
            return None
        teams = sorted(set([m["home_team"] for m in matches] + [m["away_team"] for m in matches]))
        dates = [m["date"] for m in matches]
        weights = PoissonModel.time_weight(dates)
        attack = {}
        defense = {}
        for team in teams:
            hm = [(m, w) for m, w in zip(matches, weights) if m["home_team"] == team]
            am = [(m, w) for m, w in zip(matches, weights) if m["away_team"] == team]
            scored = [m["home_goals"] for m, _ in hm] + [m["away_goals"] for m, _ in am]
            conceded = [m["away_goals"] for m, _ in hm] + [m["home_goals"] for m, _ in am]
            ws = [w for _, w in hm] + [w for _, w in am]
            if sum(ws) > 0:
                attack[team] = float(np.average(scored, weights=ws)) + 0.01
                defense[team] = float(np.average(conceded, weights=ws)) + 0.01
        if not attack or not defense:
            return None
        all_w = np.array(weights)
        hg_avg = float(np.average([m["home_goals"] for m in matches], weights=all_w))
        ag_avg = float(np.average([m["away_goals"] for m in matches], weights=all_w))
        return {
            "attack": attack,
            "defense": defense,
            "home_adv": hg_avg / max(ag_avg, 0.01),
            "avg_goals": (hg_avg + ag_avg) / 2,
            "avg_attack": float(np.mean(list(attack.values()))),
            "avg_defense": float(np.mean(list(defense.values()))),
        }

    @staticmethod
    def predict_goals(model, home, away):
        att_h = model["attack"].get(home, model["avg_attack"])
        att_a = model["attack"].get(away, model["avg_attack"])
        def_h = model["defense"].get(home, model["avg_defense"])
        def_a = model["defense"].get(away, model["avg_defense"])
        mu_h = att_h * def_a / model["avg_defense"] * model["avg_goals"] * model["home_adv"]
        mu_a = att_a * def_h / model["avg_defense"] * model["avg_goals"] / model["home_adv"]
        return max(mu_h, 0.15), max(mu_a, 0.15)

    @staticmethod
    def probabilities(mu_h, mu_a, max_g=8):
        pm = np.outer([poisson.pmf(i, mu_h) for i in range(max_g + 1)], [poisson.pmf(j, mu_a) for j in range(max_g + 1)])
        p1 = float(np.sum(np.tril(pm, -1)))
        px = float(np.sum(np.diag(pm)))
        p2 = float(np.sum(np.triu(pm, 1)))
        o25 = float(sum(pm[i][j] for i in range(max_g + 1) for j in range(max_g + 1) if i + j > 2))
        o15 = float(sum(pm[i][j] for i in range(max_g + 1) for j in range(max_g + 1) if i + j > 1))
        u35 = float(sum(pm[i][j] for i in range(max_g + 1) for j in range(max_g + 1) if i + j < 4))
        btts = float((1 - poisson.pmf(0, mu_h)) * (1 - poisson.pmf(0, mu_a)))
        score_10 = float(pm[1][0])
        score_20 = float(pm[2][0])
        score_21 = float(pm[2][1])
        score_30 = float(pm[3][0])
        score_11 = float(pm[1][1])
        score_00 = float(pm[0][0])
        home_win_to_nil = float(sum(pm[i][0] for i in range(1, max_g + 1)))
        away_win_to_nil = float(sum(pm[0][j] for j in range(1, max_g + 1)))
        return {
            "home_win": round(p1, 4),
            "draw": round(px, 4),
            "away_win": round(p2, 4),
            "over_25": round(o25, 4),
            "under_25": round(1 - o25, 4),
            "over_15": round(o15, 4),
            "under_35": round(u35, 4),
            "btts_yes": round(btts, 4),
            "score_1_0": round(score_10, 4),
            "score_2_0": round(score_20, 4),
            "score_2_1": round(score_21, 4),
            "score_3_0": round(score_30, 4),
            "score_1_1": round(score_11, 4),
            "score_0_0": round(score_00, 4),
            "home_win_to_nil": round(home_win_to_nil, 4),
            "away_win_to_nil": round(away_win_to_nil, 4),
        }


def dedupe_matches(matches):
    seen = set()
    out = []
    for m in matches:
        key = (m["date"].date().isoformat(), m["home_team_id"], m["away_team_id"], m["home_goals"], m["away_goals"])
        if key not in seen:
            seen.add(key)
            out.append(m)
    return sorted(out, key=lambda x: x["date"])


def team_form(matches, team_name, n=6):
    relevant = []
    for m in reversed(matches):
        if m["home_team"] == team_name:
            pts = 3 if m["result"] == "H" else (1 if m["result"] == "D" else 0)
            relevant.append({"pts": pts, "gf": m["home_goals"], "ga": m["away_goals"]})
        elif m["away_team"] == team_name:
            pts = 3 if m["result"] == "A" else (1 if m["result"] == "D" else 0)
            relevant.append({"pts": pts, "gf": m["away_goals"], "ga": m["home_goals"]})
        if len(relevant) >= n:
            break
    if not relevant:
        raise RuntimeError(f"Forma indisponibila pentru {team_name}")
    chars = ["W" if r["pts"] == 3 else ("D" if r["pts"] == 1 else "L") for r in relevant]
    streak = chars[0]
    streak_count = 1
    for c in chars[1:]:
        if c == streak:
            streak_count += 1
        else:
            break
    return {
        "pts": sum(r["pts"] for r in relevant),
        "gf": round(sum(r["gf"] for r in relevant) / len(relevant), 2),
        "ga": round(sum(r["ga"] for r in relevant) / len(relevant), 2),
        "streak": f"{streak_count}{streak}",
        "form_str": " ".join(reversed(chars)),
        "n": len(relevant),
    }


def h2h_stats(matches, home_id, away_id):
    hw = dx = aw = 0
    goals = []
    for m in matches:
        goals.append(m["home_goals"] + m["away_goals"])
        if m["home_team_id"] == home_id:
            if m["result"] == "H":
                hw += 1
            elif m["result"] == "D":
                dx += 1
            else:
                aw += 1
        else:
            if m["result"] == "A":
                hw += 1
            elif m["result"] == "D":
                dx += 1
            else:
                aw += 1
    return {
        "h_wins": hw,
        "draws": dx,
        "a_wins": aw,
        "n": len(matches),
        "avg_goals": round(sum(goals) / len(goals), 2) if goals else 0,
    }


def implied_odds(prob):
    return round(1 / max(prob, 0.01), 2)


def choose_safe_ticket(main_pick, main_prob, probs, home, away):
    options = [
        {"pick": main_pick, "prob": main_prob, "market": "1X2"},
        {"pick": "Over 1.5", "prob": probs["over_15"], "market": "goals"},
        {"pick": "Under 3.5", "prob": probs["under_35"], "market": "goals"},
    ]
    best = max(options, key=lambda x: x["prob"])
    best["odds_model"] = implied_odds(best["prob"])
    return best


def choose_balanced_ticket(main_pick, main_prob, probs, home, away):
    combos = []
    if main_pick.startswith("1"):
        combos.append({
            "pick": "1 & Over 1.5",
            "prob": round(main_prob * probs["over_15"], 4),
            "market": "combo"
        })
        combos.append({
            "pick": "1 & Under 3.5",
            "prob": round(main_prob * probs["under_35"], 4),
            "market": "combo"
        })
        combos.append({
            "pick": f"{home} castiga fara gol primit",
            "prob": probs["home_win_to_nil"],
            "market": "win_to_nil"
        })
    elif main_pick.startswith("2"):
        combos.append({
            "pick": "2 & Over 1.5",
            "prob": round(main_prob * probs["over_15"], 4),
            "market": "combo"
        })
        combos.append({
            "pick": "2 & Under 3.5",
            "prob": round(main_prob * probs["under_35"], 4),
            "market": "combo"
        })
        combos.append({
            "pick": f"{away} castiga fara gol primit",
            "prob": probs["away_win_to_nil"],
            "market": "win_to_nil"
        })
    else:
        combos.append({"pick": "X & Under 3.5", "prob": round(main_prob * probs["under_35"], 4), "market": "combo"})
        combos.append({"pick": "X & Under 2.5", "prob": round(main_prob * probs["under_25"], 4), "market": "combo"})
        combos.append({"pick": "0-0", "prob": probs["score_0_0"], "market": "correct_score"})
        combos.append({"pick": "1-1", "prob": probs["score_1_1"], "market": "correct_score"})
    best = max(combos, key=lambda x: x["prob"])
    best["odds_model"] = implied_odds(best["prob"])
    return best


def choose_aggressive_ticket(main_pick, probs, home, away):
    if main_pick.startswith("1"):
        opts = [
            {"pick": "2-0", "prob": probs["score_2_0"], "market": "correct_score"},
            {"pick": "2-1", "prob": probs["score_2_1"], "market": "correct_score"},
            {"pick": "3-0", "prob": probs["score_3_0"], "market": "correct_score"},
            {"pick": f"{home} castiga fara gol primit", "prob": probs["home_win_to_nil"], "market": "win_to_nil"},
        ]
    elif main_pick.startswith("2"):
        opts = [
            {"pick": "0-1", "prob": probs["score_1_0"], "market": "correct_score_proxy"},
            {"pick": f"{away} castiga fara gol primit", "prob": probs["away_win_to_nil"], "market": "win_to_nil"},
        ]
    else:
        opts = [
            {"pick": "0-0", "prob": probs["score_0_0"], "market": "correct_score"},
            {"pick": "1-1", "prob": probs["score_1_1"], "market": "correct_score"},
        ]
    best = max(opts, key=lambda x: x["prob"])
    best["odds_model"] = implied_odds(best["prob"])
    return best


class BettingAnalyzer:
    def __init__(self, competition_codes, days=7, start_date=None):
        self.fd = FootballDataFetcher()
        self.tsdb = TheSportsDBEnricher()
        self.competition_codes = competition_codes
        self.date_str = start_date or datetime.now(timezone.utc).date().isoformat()
        self.days = days
        self.match_analysis = []
        self.tickets = []

    def standings_boost(self, comp_code, home_id, away_id):
        try:
            table = self.fd.get_competition_standings(comp_code)
        except Exception:
            return 1.0, 1.0, {"home": {}, "away": {}}

        h = table.get(home_id, {})
        a = table.get(away_id, {})

        if not h or not a:
            return 1.0, 1.0, {"home": h, "away": a}

        rank_gap = (a.get("position", 0) or 0) - (h.get("position", 0) or 0)
        gd_gap = (h.get("goalDifference", 0) or 0) - (a.get("goalDifference", 0) or 0)
        pts_gap = (h.get("points", 0) or 0) - (a.get("points", 0) or 0)

        boost_h = 1.0
        boost_a = 1.0

        boost_h *= 1 + max(min(rank_gap * 0.01, 0.08), -0.08)
        boost_a *= 1 + max(min(-rank_gap * 0.01, 0.08), -0.08)

        boost_h *= 1 + max(min(gd_gap * 0.002, 0.05), -0.05)
        boost_a *= 1 + max(min(-gd_gap * 0.002, 0.05), -0.05)

        boost_h *= 1 + max(min(pts_gap * 0.0015, 0.05), -0.05)
        boost_a *= 1 + max(min(-pts_gap * 0.0015, 0.05), -0.05)

        return round(boost_h, 4), round(boost_a, 4), {"home": h, "away": a}

    def load_matches(self):
        header(f"PASUL 1: MECIURILE DIN URMATOARELE {self.days} ZILE")
        matches = self.fd.get_week_matches(self.competition_codes, self.date_str, self.days)
        if not matches:
            raise RuntimeError(f"Nu am gasit meciuri in urmatoarele {self.days} zile pentru competitiile selectate.")
        ok(f"Meciuri gasite: {len(matches)}")
        return matches

    def analyze_match(self, match):
        home = match["home_team"]
        away = match["away_team"]
        home_id = match["home_team_id"]
        away_id = match["away_team_id"]
        comp = match["competition_code"]
        match_id = match["match_id"]

        home_hist = self.fd.get_team_matches(home_id, limit=30, lookback_days=730)
        away_hist = self.fd.get_team_matches(away_id, limit=30, lookback_days=730)
        if len(home_hist) < 8 or len(away_hist) < 8:
            home_hist = self.fd.get_team_matches(home_id, limit=50, lookback_days=1460)
            away_hist = self.fd.get_team_matches(away_id, limit=50, lookback_days=1460)
        if len(home_hist) < 6 or len(away_hist) < 6:
            raise RuntimeError(
                f"Istoric insuficient pentru {home} vs {away} (home={len(home_hist)}, away={len(away_hist)})"
            )

        combined = dedupe_matches(home_hist + away_hist)
        model = PoissonModel.fit(combined)
        if not model:
            raise RuntimeError(
                f"Model indisponibil pentru {home} vs {away} (meciuri combinate={len(combined)})"
            )

        mu_h, mu_a = PoissonModel.predict_goals(model, home, away)
        form_h = team_form(home_hist, home)
        form_a = team_form(away_hist, away)
        mu_h *= max(0.90, min(1.10, 1.0 + (form_h["pts"] - form_a["pts"]) * 0.01))
        mu_a *= max(0.90, min(1.10, 1.0 + (form_a["pts"] - form_h["pts"]) * 0.01))
        boost_h, boost_a, standings_meta = self.standings_boost(comp, home_id, away_id)
        mu_h *= boost_h
        mu_a *= boost_a
        probs = PoissonModel.probabilities(mu_h, mu_a)

        max_p = max(probs["home_win"], probs["draw"], probs["away_win"])
        if probs["home_win"] == max_p:
            main_pick, main_prob = f"1 ({home})", probs["home_win"]
        elif probs["away_win"] == max_p:
            main_pick, main_prob = f"2 ({away})", probs["away_win"]
        else:
            main_pick, main_prob = "X (Egal)", probs["draw"]

        if probs["over_25"] >= 0.60:
            goals_pick, goals_prob = "Over 2.5", probs["over_25"]
        elif probs["btts_yes"] >= 0.58:
            goals_pick, goals_prob = "BTTS Da", probs["btts_yes"]
        elif probs["under_25"] >= 0.60:
            goals_pick, goals_prob = "Under 2.5", probs["under_25"]
        else:
            goals_pick, goals_prob = "Over 1.5", probs["over_15"]

        h2h = self.fd.get_match_head2head(match_id, limit=10)
        h2h_meta = h2h_stats(h2h, home_id, away_id)
        enrichment = self.tsdb.enrich_match(home, away)

        ticket_model = {
            "safe": choose_safe_ticket(main_pick, main_prob, probs, home, away),
            "balanced": choose_balanced_ticket(main_pick, main_prob, probs, home, away),
            "aggressive": choose_aggressive_ticket(main_pick, probs, home, away),
        }

        return {
            "match": f"{home} vs {away}",
            "match_id": match_id,
            "league": match["league"],
            "competition_code": comp,
            "kickoff": match["utcDate"],
            "status": match["status"],
            "home_team": home,
            "away_team": away,
            "home_team_id": home_id,
            "away_team_id": away_id,
            "mu_home": round(mu_h, 2),
            "mu_away": round(mu_a, 2),
            "probs": probs,
            "main_pick": main_pick,
            "main_prob": round(main_prob, 4),
            "main_odds_model": implied_odds(main_prob),
            "goals_pick": goals_pick,
            "goals_prob": round(goals_prob, 4),
            "goals_odds_model": implied_odds(goals_prob),
            "confidence": round(max_p * 100, 1),
            "form_home": form_h,
            "form_away": form_a,
            "standings": standings_meta,
            "h2h": h2h_meta,
            "hist_n": len(combined),
            "enrichment": enrichment,
            "ticket_model": ticket_model,
        }

    def run_analysis(self, matches):
        header("PASUL 2: ANALIZA STATISTICA")
        for m in matches:
            section(f"{m['home_team']} vs {m['away_team']} [{m['league']}]")
            try:
                a = self.analyze_match(m)
                self.match_analysis.append(a)
                ok(
                    f"xG {a['mu_home']:.2f}-{a['mu_away']:.2f} | "
                    f"1:{a['probs']['home_win']:.0%} X:{a['probs']['draw']:.0%} 2:{a['probs']['away_win']:.0%} | "
                    f"Conf:{a['confidence']:.0f}%"
                )
            except Exception as e:
                err(str(e))
        if not self.match_analysis:
            raise RuntimeError("Niciun meci nu a putut fi analizat corect.")

    def build_tickets(self):
        by_conf = sorted(self.match_analysis, key=lambda x: x["confidence"], reverse=True)
        by_goals = sorted(self.match_analysis, key=lambda x: x["goals_prob"], reverse=True)
        by_value = sorted(self.match_analysis, key=lambda x: x["main_odds_model"], reverse=True)

        def pick_row(m, use_goals=False):
            return {
                "league": m["league"],
                "match": m["match"],
                "kickoff": m["kickoff"],
                "pick": m["goals_pick"] if use_goals else m["main_pick"],
                "prob": m["goals_prob"] if use_goals else m["main_prob"],
                "odds": m["goals_odds_model"] if use_goals else m["main_odds_model"],
                "conf": m["confidence"],
            }

        t1 = [pick_row(m) for m in by_conf[:4]]
        used = {m["match"] for m in by_conf[:3]}
        t2 = [pick_row(m) for m in by_conf[:3]] + [pick_row(m, True) for m in by_goals if m["match"] not in used][:2]
        t3 = [pick_row(m) for m in by_value if m["main_prob"] >= 0.30][:4]

        self.tickets = [
            {"name": "BILET 1 -- SIGUR", "style": "Top selectii dupa incredere", "picks": t1},
            {"name": "BILET 2 -- ECHILIBRAT", "style": "Mix 1X2 si piete goluri", "picks": t2},
            {"name": "BILET 3 -- COTA MAI MARE", "style": "Selectii cu cota model mai ridicata", "picks": t3},
        ]

        for t in self.tickets:
            total_odds = 1.0
            combined_prob = 1.0
            for p in t["picks"]:
                total_odds *= p["odds"]
                combined_prob *= p["prob"]
            t["total_odds"] = round(total_odds, 2)
            t["combined_prob"] = round(combined_prob * 100, 2)

    def print_analysis(self):
        header("ANALIZA MECIURILOR")
        rows = []
        for m in self.match_analysis:
            rows.append([
                m["competition_code"], m["home_team"][:17], m["away_team"][:17],
                f"{m['mu_home']:.2f}", f"{m['mu_away']:.2f}",
                f"{m['probs']['home_win']:.0%}", f"{m['probs']['draw']:.0%}", f"{m['probs']['away_win']:.0%}",
                f"{m['probs']['over_25']:.0%}", f"{m['probs']['btts_yes']:.0%}", f"{m['confidence']:.0f}%",
            ])
        print(tabulate(rows, headers=["Liga", "Acasa", "Deplasare", "xG-H", "xG-A", "1", "X", "2", "O2.5", "BTTS", "Conf."], tablefmt="rounded_outline"))

    def print_form(self):
        header("FORMA, STANDINGS SI H2H")
        rows = []
        for m in self.match_analysis:
            sh = m["standings"]["home"]
            sa = m["standings"]["away"]
            rows.append([
                m["home_team"][:14],
                m["form_home"]["form_str"][:13],
                m["form_home"]["streak"],
                sh.get("position") if sh else None,
                m["away_team"][:14],
                m["form_away"]["form_str"][:13],
                m["form_away"]["streak"],
                sa.get("position") if sa else None,
                m["h2h"]["n"],
                m["h2h"]["avg_goals"],
            ])
        print(tabulate(rows, headers=["Acasa", "Forma-H", "Serie-H", "Poz-H", "Dep.", "Forma-A", "Serie-A", "Poz-A", "H2H N", "H2H AvgG"], tablefmt="rounded_outline"))

    def print_per_match_ticket_models(self):
        header("MODEL DE BILET PE FIECARE MECI")
        for m in self.match_analysis:
            print(Fore.CYAN + f"\n{m['match']} [{m['league']}]")
            print(Fore.CYAN + f"Kickoff: {m['kickoff']} | xG: {m['mu_home']:.2f}-{m['mu_away']:.2f}")
            tm = m["ticket_model"]
            rows = [
                ["SAFE", tm["safe"]["pick"], f"{tm['safe']['prob']:.0%}", f"@{tm['safe']['odds_model']:.2f}", tm['safe']['market']],
                ["BALANCED", tm["balanced"]["pick"], f"{tm['balanced']['prob']:.0%}", f"@{tm['balanced']['odds_model']:.2f}", tm['balanced']['market']],
                ["AGGRESSIVE", tm["aggressive"]["pick"], f"{tm['aggressive']['prob']:.0%}", f"@{tm['aggressive']['odds_model']:.2f}", tm['aggressive']['market']],
            ]
            print(tabulate(rows, headers=["Model", "Selectie", "Prob", "Cota model", "Tip piata"], tablefmt="simple"))

    def print_tickets(self):
        header("CELE 3 BILETE")
        colors = [Fore.GREEN, Fore.YELLOW, Fore.RED]
        for i, t in enumerate(self.tickets):
            c = colors[i]
            print(f"\n{c}{'=' * 80}")
            print(f"{c}  {t['name']}")
            print(f"{c}  {t['style']}")
            print(f"{c}{'-' * 80}")
            rows = []
            for p in t["picks"]:
                hh = p["kickoff"][11:16] if p["kickoff"] and len(p["kickoff"]) >= 16 else "?"
                rows.append([
                    p["league"][:18],
                    p["match"][:34],
                    hh,
                    p["pick"],
                    f"{p['prob']:.0%}",
                    f"@{p['odds']:.2f}",
                    f"{p['conf']:.0f}%",
                ])
            print(tabulate(rows, headers=["Liga", "Meci", "Ora", "Selectie", "Prob", "Cota", "Conf."], tablefmt="simple"))
            print(f"{c}  Selectii:{len(t['picks'])}  Cota:{t['total_odds']:.2f}x  Prob:{t['combined_prob']:.1f}%")
            print(f"{c}{'=' * 80}")

    def save_json(self):
        os.makedirs("output", exist_ok=True)
        path = f"output/analiza_hybrid_{self.date_str}_{self.days}zile_v54.json"
        data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "start_date": self.date_str,
            "days": self.days,
            "competitions": self.competition_codes,
            "matches": self.match_analysis,
            "tickets": self.tickets,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        ok(f"Salvat: {path}")

    def run(self):
        print(
            Fore.CYAN
            + Style.BRIGHT
            + """
+--------------------------------------------------------------------------------+
|  FOOTBALL BETTING ANALYZER v5.4 -- HYBRID FREE / N ZILE                        |
|  football-data.org + TheSportsDB | Poisson + Forma + Standings + H2H           |
|  + ticket_model per meci (safe / balanced / aggressive)                        |
+--------------------------------------------------------------------------------+"""
        )
        ok(f"Start analiza: {self.date_str}")
        ok(f"Interval zile: {self.days}")
        ok(f"Competitii: {self.competition_codes}")
        matches = self.load_matches()
        self.run_analysis(matches)
        self.print_analysis()
        self.print_form()
        self.print_per_match_ticket_models()
        self.build_tickets()
        self.print_tickets()
       # self.save_json()
        print(Fore.MAGENTA + "\n  Pariat responsabil. Model statistic, fara garantii de profit.\n")


if __name__ == "__main__":
    ensure_fd_key()
    parser = argparse.ArgumentParser(description="Football Betting Analyzer v5.4")
    parser.add_argument(
        "--competitions",
        nargs="*",
        default=DEFAULT_COMPETITIONS,
        help="Coduri competitii football-data.org, ex: PL PD SA BL1 FL1 CL",
    )
    parser.add_argument("--days", type=int, default=7, help="Numar de zile de la data de start")
    parser.add_argument(
        "--start-date",
        default=datetime.now(timezone.utc).date().isoformat(),
        help="Data de start in format YYYY-MM-DD",
    )
    args, _ = parser.parse_known_args(sys.argv[1:])
    analyzer = BettingAnalyzer(
        competition_codes=args.competitions or DEFAULT_COMPETITIONS,
        days=args.days,
        start_date=args.start_date,
    )
    analyzer.run()
