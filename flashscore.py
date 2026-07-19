"""
============================================================
  FOOTBALL BETTING ANALYZER (HTML) v3.0 - by Costin Picu
  Sursa meciuri: Flashscore (pagini fixtures HTML)
  Model:        Poisson + Forma + Accidentari (istoric sintetic)
  Output:       3 bilete in consola + JSON

IMPORTANT:
  - Nu mai folosim feed-ul intern "d.flashscore.com" si x-fsign.
  - Luam meciurile din paginile HTML fixtures, randate in browser,
    cu Playwright + BeautifulSoup (exact cum recomanda multe
    exemple de scraping Flashscore din comunitate). [web:258][web:61]
  - Istoricul si forma sunt sintetice (random Poisson) ca sa existe
    un model statistic, dar NU sunt date reale; poti inlocui ulterior
    cu surse reale (API Football etc).
============================================================

Instalare:
  pip install playwright beautifulsoup4 numpy scipy tabulate colorama
  playwright install chromium

Rulare:
  python flashscore_analyzer_html.py
  python flashscore_analyzer_html.py --leagues Romania "Premier League"
"""

import os
import json
import warnings
import random
import concurrent.futures

from datetime import datetime, timedelta

import numpy as np
from scipy.stats import poisson
from tabulate import tabulate
from colorama import Fore, Style, init
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")
init(autoreset=True)

# -------------------------------------------------------------------
# CONFIG: ligile si URL-urile lor de fixtures de pe Flashscore.
# Poti ajusta liber dictionarul LEAGUE_URLS dupa cum ai nevoie:
# - URL-uri de forma /football/{tara}/{competitie}/fixtures/
# Exemplu: Premier League results/fixtures din StackOverflow [web:258]
# -------------------------------------------------------------------

LEAGUE_URLS = {
    "Romania": "https://www.flashscore.ro/fotbal/romania/superliga/",
    "Cupa mondiala":"https://www.flashscore.ro/fotbal/lume/cupa-mondiala/meciuri/"
   
}


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def header(t):
    print()
    print(Fore.CYAN + "=" * 64)
    print(Fore.CYAN + f"  {t}")
    print(Fore.CYAN + "=" * 64)


def section(t):
    print(Fore.YELLOW + f"\n  >> {t}")


def ok(t):
    print(Fore.GREEN + f"     [OK] {t}")


def warn(t):
    print(Fore.YELLOW + f"     [!]  {t}")


def err(t):
    print(Fore.RED + f"     [ERR] {t}")


# -------------------------------------------------------------------
#  FIXTURES SCRAPER - Playwright + BeautifulSoup
#  - Deschide URL-urile fixtures in Chromium headless
#  - Asteapta randarea JS
#  - Extrage meciurile folosind CSS selectorii testati public
#    (event__time, event__participant--home/away, event__scores)
#    asa cum se arata in raspunsurile de pe StackOverflow. [web:258][web:61]
# -------------------------------------------------------------------
class FixturesScraper:
    def __init__(self, timeout_s=25):
        self.timeout_s = timeout_s

    def _fetch_html_in_thread(self, url):
        """
        Playwright Sync API nu merge direct in Jupyter (are asyncio loop activ).
        Solutie: rulam fetch-ul intr-un thread nou, fara loop asyncio,
        folosind un ThreadPoolExecutor.
        """
        import concurrent.futures

        def _do_fetch():
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                    locale="ro-RO",
                )
                page = context.new_page()
                page.goto(url, timeout=self.timeout_s * 1000)

                try:
                    page.wait_for_selector("[id^=g_]", timeout=self.timeout_s * 1000)
                except Exception:
                    page.wait_for_timeout(5000)

                html = page.content()
                browser.close()
                return html

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_do_fetch)
            try:
                return future.result(timeout=self.timeout_s + 15)
            except Exception as e:
                err(f"[!] Fetch HTML esuat pentru {url}: {e}")
                return ""





    def fetch_html(self, url):
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self._fetch_html_in_thread, url)
                html = future.result(timeout=self.timeout_s + 10)
            return html
        except Exception as e:
            warn(f"Fetch HTML esuat pentru {url}: {e}")
            return ""

    def parse_fixtures(self, league_name, html):
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        match_divs = soup.select("[id^=g_]")

        matches = []
        for div in match_divs:
            home_el = div.select_one(".event__homeParticipant")
            away_el = div.select_one(".event__awayParticipant")
            time_el = div.select_one(".event__stageTime")
            row_link_el = div.select_one("a.eventRowLink")

            if not home_el or not away_el:
                continue

            home = home_el.get_text(strip=True)
            away = away_el.get_text(strip=True)
            time_txt = time_el.get_text(strip=True) if time_el else ""

            # NOU: calculam data/ora completa a meciului
            match_dt = None
            if time_el:
                raw_title = time_el.get("title")
                raw_date = raw_title or time_txt
                match_dt = self._parse_match_datetime(raw_date)

            if not home or not away:
                continue

            home_url, away_url = None, None
            if row_link_el and row_link_el.has_attr("href"):
                match_url = row_link_el["href"]
                parts = match_url.rstrip("/").split("/")
                if len(parts) >= 2:
                    away_slug = parts[-1].split("?")[0]
                    home_slug = parts[-2]
                    base = "/".join(parts[:-2])
                    home_url = f"{base}/{home_slug}/"
                    away_url = f"{base}/{away_slug}/"

            matches.append({
                "league": league_name,
                "home_team": home,
                "away_team": away,
                "kickoff": time_txt,
                "match_datetime": match_dt,   # <-- ACUM ESTE SETAT
                "home_team_url": home_url,
                "away_team_url": away_url,
                "raw_score": "",
            })

        return matches



    def _parse_match_datetime(self, raw):
        """
        Incearca sa parseze diverse formate de data/ora Flashscore.
        Returneaza datetime sau None daca nu poate.
        """
        if not raw:
            return None

        now = datetime.now()
        raw = raw.strip()

        formats_with_year = ["%d.%m.%Y %H:%M", "%d.%m.%Y"]
        formats_no_year = ["%d.%m. %H:%M", "%d.%m."]
        time_only_formats = ["%H:%M"]

        for fmt in formats_with_year:
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                pass

        for fmt in formats_no_year:
            try:
                dt = datetime.strptime(raw, fmt)
                return dt.replace(year=now.year)
            except ValueError:
                pass

        for fmt in time_only_formats:
            try:
                dt = datetime.strptime(raw, fmt)
                # Presupunem ca meciul e azi, la ora respectiva
                return now.replace(
                    hour=dt.hour, minute=dt.minute, second=0, microsecond=0
                )
            except ValueError:
                pass

        return None






    def get_league_fixtures(self, league_name, url):
        section(f"Scraping fixtures Flashscore pentru {league_name}")
        html = self.fetch_html(url)
        fixtures = self.parse_fixtures(league_name, html)
        ok(f"{league_name}: gasite {len(fixtures)} meciuri (nu filtram strict pe 'azi').")
        html = self._fetch_html_in_thread(url)
      #  print("DEBUG: primele 1000 de caractere din HTML:\n", html[:1000])

        return fixtures


# -------------------------------------------------------------------
#  MODEL POISSON + FORMA + ACCIDENTARI
#  (istoric sintetic - explicit random, nu date reale)
# -------------------------------------------------------------------
class PoissonModel:
    @staticmethod
    def time_weight(dates, xi=0.012):
        if not dates:
            return np.ones(1)
        latest = max(dates)
        return np.array([np.exp(-xi * ((latest - d).days / 3.5)) for d in dates])

    @staticmethod
    def fit(matches):
        if not matches or len(matches) < 5:
            return None
        teams = sorted(set(
            [m["home_team"] for m in matches] +
            [m["away_team"] for m in matches]
        ))
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
            else:
                attack[team] = 1.4
                defense[team] = 1.2

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
            "n_matches": len(matches),
        }

    @staticmethod
    def predict_goals(model, home, away):
        if not model:
            return 1.5, 1.1
        att_h = model["attack"].get(home, model["avg_attack"])
        att_a = model["attack"].get(away, model["avg_attack"])
        def_h = model["defense"].get(home, model["avg_defense"])
        def_a = model["defense"].get(away, model["avg_defense"])

        mu_h = att_h * def_a / model["avg_defense"] * model["avg_goals"] * model["home_adv"]
        mu_a = att_a * def_h / model["avg_defense"] * model["avg_goals"] / model["home_adv"]
        return max(mu_h, 0.15), max(mu_a, 0.15)

    @staticmethod
    def probabilities(mu_h, mu_a, max_g=8):
        pm = np.outer(
            [poisson.pmf(i, mu_h) for i in range(max_g + 1)],
            [poisson.pmf(j, mu_a) for j in range(max_g + 1)],
        )
        p1 = float(np.sum(np.tril(pm, -1)))
        px = float(np.sum(np.diag(pm)))
        p2 = float(np.sum(np.triu(pm, 1)))

        o25 = float(sum(pm[i][j] for i in range(max_g + 1) for j in range(max_g + 1) if i + j > 2))
        o15 = float(sum(pm[i][j] for i in range(max_g + 1) for j in range(max_g + 1) if i + j > 1))
        btts = float((1 - poisson.pmf(0, mu_h)) * (1 - poisson.pmf(0, mu_a)))

        return {
            "home_win": round(p1, 4),
            "draw": round(px, 4),
            "away_win": round(p2, 4),
            "over_25": round(o25, 4),
            "under_25": round(1 - o25, 4),
            "over_15": round(o15, 4),
            "btts_yes": round(btts, 4),
            "mu_home": round(mu_h, 2),
            "mu_away": round(mu_a, 2),
        }


def team_form(matches, team, n=6):
    """
    Forma este calculata pe baza ISTORICULUI SINTETIC, nu real.
    """
    relevant = []
    for m in reversed(matches):
        if m["home_team"] == team:
            pts = 3 if m["result"] == "H" else (1 if m["result"] == "D" else 0)
            relevant.append({"pts": pts, "gf": m["home_goals"], "ga": m["away_goals"]})
        elif m["away_team"] == team:
            pts = 3 if m["result"] == "A" else (1 if m["result"] == "D" else 0)
            relevant.append({"pts": pts, "gf": m["away_goals"], "ga": m["home_goals"]})
        if len(relevant) >= n:
            break

    if not relevant:
        return {"pts": 0, "gf": 0, "ga": 0, "streak": "?", "form_str": "?", "n": 0}

    chars = ["W" if r["pts"] == 3 else ("D" if r["pts"] == 1 else "L") for r in relevant]
    sc = chars[0]
    cnt = 1
    for c in chars[1:]:
        if c == sc:
            cnt += 1
        else:
            break

    return {
        "pts": sum(r["pts"] for r in relevant),
        "gf": round(sum(r["gf"] for r in relevant) / len(relevant), 2),
        "ga": round(sum(r["ga"] for r in relevant) / len(relevant), 2),
        "streak": f"{cnt}{sc}",
        "form_str": " ".join(reversed(chars)),
        "n": len(relevant),
    }


def injury_factor(n):
    return [1.0, 0.97, 0.93, 0.88, 0.82][min(n, 4)]


def impl_odds(p):
    return round(1 / max(p, 0.01), 2)

class TeamHistoryScraper:
    def __init__(self, timeout_s=25):
        self.timeout_s = timeout_s
        self._fetcher = FixturesScraper(timeout_s=timeout_s)  # reutilizam fetch-ul cu Playwright

    def get_team_results_url(self, team_url_base):
        """
        team_url_base: url de forma https://www.flashscore.ro/echipa/XXXX/YYYY/
        Adaugam /rezultate/ la finalul lui.
        """
        return team_url_base.rstrip("/") + "/rezultate/"

    def fetch_team_history(self, team_name, team_url):
        url = self.get_team_results_url(team_url)
        html = self._fetcher._fetch_html_in_thread(url)
        return self.parse_team_history(team_name, html)

    def parse_team_history(self, team_name, html, max_matches=24):
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        match_divs = soup.select("[id^=g_]")

        history = []
        for div in match_divs[:max_matches]:
            home_el = div.select_one(".event__homeParticipant")
            away_el = div.select_one(".event__awayParticipant")
            score_home_el = div.select_one(".event__score--home")
            score_away_el = div.select_one(".event__score--away")
            time_el = div.select_one(".event__stageTime")

            if not home_el or not away_el or not score_home_el or not score_away_el:
                continue

            try:
                hg = int(score_home_el.get_text(strip=True))
                ag = int(score_away_el.get_text(strip=True))
            except ValueError:
                continue

            home = home_el.get_text(strip=True)
            away = away_el.get_text(strip=True)
            date_txt = time_el.get("title") if time_el else None
            match_date = self._fetcher._parse_match_datetime(date_txt) or datetime.now()

            res = "H" if hg > ag else ("A" if ag > hg else "D")

            history.append({
                "date": match_date,
                "home_team": home,
                "away_team": away,
                "home_goals": hg,
                "away_goals": ag,
                "result": res,
            })

        return history


# -------------------------------------------------------------------
#  ANALYZER: porneste scraper-ul, ruleaza modelul, face cele 3 bilete
# -------------------------------------------------------------------
class BettingAnalyzer:
    def __init__(self, leagues_filter=None):
        self.scraper = FixturesScraper()
        self.history_scraper = TeamHistoryScraper()   # doar aici
        self.leagues_filter = leagues_filter
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.match_analysis = []
        self.tickets = []

    def load_today_matches(self, days_window=3):
        header("PASUL 1: Scraping meciuri (fixtures) de pe Flashscore")

        target_leagues = self.leagues_filter or list(LEAGUE_URLS.keys())
        all_matches = []

        for lname in target_leagues:
            url = LEAGUE_URLS.get(lname)
            if not url:
                warn(f"Niciun URL configurat pentru liga '{lname}' - skip.")
                continue
            fixtures = self.scraper.get_league_fixtures(lname, url)
            all_matches.extend(fixtures)

        now = datetime.now()
        cutoff = now + timedelta(days=days_window)

        filtered = []
        for m in all_matches:
            dt = m.get("match_datetime")
            if dt is None:
                # Daca nu avem data, il pastram (fallback), dar il marcam
                filtered.append(m)
                continue
            if now - timedelta(hours=2) <= dt <= cutoff:
                filtered.append(m)

        ok(f"Total meciuri extrase din HTML: {len(all_matches)}")
        ok(f"Meciuri in urmatoarele {days_window} zile: {len(filtered)}")

        return filtered

    def _synthetic_history(self, team, n=24):
        """
        Istoric sintetic: random Poisson, explicit fictiv.
        Nu este bazat pe date reale, ci doar pentru a avea ceva
        pe care sa calibram modelul Poisson.
        """
        np.random.seed(abs(hash(team)) % (2 ** 31))
        base = datetime.now() - timedelta(days=n * 7)
        opp = ["Team A", "Team B", "Team C", "Team D", "Team E"]
        out = []
        for i in range(n):
            hg = int(np.random.poisson(1.5))
            ag = int(np.random.poisson(1.1))
            res = "H" if hg > ag else ("A" if ag > hg else "D")
            out.append({
                "date": base + timedelta(days=i * 7),
                "home_team": team if i % 2 == 0 else opp[i % 5],
                "away_team": opp[i % 5] if i % 2 == 0 else team,
                "home_goals": hg,
                "away_goals": ag,
                "result": res,
            })
        return out

    def analyze_match(self, match):
        home = match["home_team"]
        away = match["away_team"]

        home_url = match.get("home_team_url")
        away_url = match.get("away_team_url")

        if not home_url or not away_url:
            warn(f"  URL echipa lipsa pentru {home} / {away}, folosesc istoric sintetic fallback.")
            h_hist = self._synthetic_history(home)
            a_hist = self._synthetic_history(away)
        else:
            h_hist = self.history_scraper.fetch_team_history(home, home_url)
            a_hist = self.history_scraper.fetch_team_history(away, away_url)
            if not h_hist:
                h_hist = self._synthetic_history(home)
            if not a_hist:
                a_hist = self._synthetic_history(away)

        all_hist = sorted(h_hist + a_hist, key=lambda x: x["date"])
        
        model = PoissonModel.fit(all_hist)

        model = PoissonModel.fit(all_hist)
        mu_h, mu_a = PoissonModel.predict_goals(model, home, away)

        form_h = team_form(all_hist, home)
        form_a = team_form(all_hist, away)

        # Ajustam xG dupa forma (fictiv)
        mu_h = max(0.2, mu_h * (1.0 + (form_h["pts"] - 9) * 0.01))
        mu_a = max(0.2, mu_a * (1.0 + (form_a["pts"] - 9) * 0.01))

        # Accidentari artificiale (random)
        n_inj_h = random.randint(0, 3)
        n_inj_a = random.randint(0, 3)
        mu_h *= injury_factor(n_inj_h)
        mu_a *= injury_factor(n_inj_a)

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
        elif probs["btts_yes"] >= 0.56:
            goals_pick, goals_prob = "BTTS Da", probs["btts_yes"]
        elif probs["under_25"] >= 0.60:
            goals_pick, goals_prob = "Under 2.5", probs["under_25"]
        else:
            goals_pick, goals_prob = "Over 1.5", probs["over_15"]

        # Cote model (nu bookmaker) - strict implicite din probabilitati
        main_odds = impl_odds(main_prob)
        goals_odds = impl_odds(goals_prob)

        return {
            "match": f"{home} vs {away}",
            "home_team": home,
            "away_team": away,
            "league": match["league"],
            "kickoff": match["kickoff"],
            "mu_home": round(mu_h, 2),
            "mu_away": round(mu_a, 2),
            "probs": probs,
            "main_pick": main_pick,
            "main_prob": round(main_prob, 4),
            "main_odds": main_odds,
            "goals_pick": goals_pick,
            "goals_prob": round(goals_prob, 4),
            "goals_odds": goals_odds,
            "confidence": round(max_p * 100, 1),
            "form_home": form_h,
            "form_away": form_a,
            "n_inj_home": n_inj_h,
            "n_inj_away": n_inj_a,
            "used_synthetic_history": False,
        }

    def run_analysis(self, matches):
        header("PASUL 2: Analiza Poisson + Forma + Accidentari")
        for m in matches:
            section(f"{m['home_team']} vs {m['away_team']}  [{m['league']}]")
            try:
                a = self.analyze_match(m)
                self.match_analysis.append(a)
                ok(
                    f"  xG:{a['mu_home']:.2f}-{a['mu_away']:.2f} | "
                    f"1:{a['probs']['home_win']:.0%} X:{a['probs']['draw']:.0%} 2:{a['probs']['away_win']:.0%} | "
                    f"Conf:{a['confidence']:.0f}% [MODEL, ISTORIC SINTETIC]"
                )
            except Exception as e:
                err(f"  Eroare analiza: {e}")

    def build_tickets(self):
        if not self.match_analysis:
            return []

        by_conf = sorted(self.match_analysis, key=lambda x: x["confidence"], reverse=True)
        by_odds = sorted(self.match_analysis, key=lambda x: x["main_odds"], reverse=True)
        by_goals = sorted(self.match_analysis, key=lambda x: x["goals_prob"], reverse=True)

        def mk(m, goals=False):
            return {
                "match": m["match"],
                "league": m["league"],
                "kickoff": m["kickoff"],
                "pick": m["goals_pick"] if goals else m["main_pick"],
                "prob": m["goals_prob"] if goals else m["main_prob"],
                "odds": m["goals_odds"] if goals else m["main_odds"],
                "conf": m["confidence"],
            }

        t1 = [mk(m) for m in by_conf[:4]]
        used = {m["match"] for m in by_conf[:3]}
        t2 = [mk(m) for m in by_conf[:3]] + [
            mk(m, True) for m in by_goals if m["match"] not in used
        ][:2]

        vb = [mk(m) for m in self.match_analysis if m["main_odds"] >= 2.5]
        used_v = {p["match"] for p in vb}
        vb += [
            mk(m) for m in by_odds
            if m["match"] not in used_v and m["main_prob"] >= 0.30
        ][:4 - len(vb)]
        t3 = vb[:4]

        tickets = [
            {
                "name": "BILET 1 -- SIGUR (model, cote implicite)",
                "style": "Top 4 selectii probabilitate maxima",
                "picks": t1,
            },
            {
                "name": "BILET 2 -- ECHILIBRAT (mix 1X2 + goluri)",
                "style": "Mix rezultate finale + goluri",
                "picks": t2,
            },
            {
                "name": "BILET 3 -- VALUE (model, cote mari)",
                "style": "Selectii cu cote mai mari (model), risc ridicat",
                "picks": t3,
            },
        ]

        for t in tickets:
            op = 1.0
            cp = 1.0
            for p in t["picks"]:
                op *= p["odds"]
                cp *= p["prob"]
            t["total_odds"] = round(op, 2)
            t["combined_prob"] = round(cp * 100, 2)

        return tickets

    def print_analysis(self):
        header("ANALIZA MECIURI (MODEL)")
        rows = []
        for m in self.match_analysis:
            rows.append([
                m["league"][:18],
                m["home_team"][:15],
                m["away_team"][:15],
                f"{m['mu_home']:.2f}",
                f"{m['mu_away']:.2f}",
                f"{m['probs']['home_win']:.0%}",
                f"{m['probs']['draw']:.0%}",
                f"{m['probs']['away_win']:.0%}",
                f"{m['probs']['over_25']:.0%}",
                f"{m['probs']['btts_yes']:.0%}",
                f"{m['confidence']:.0f}%",
            ])
        print(
            tabulate(
                rows,
                headers=[
                    "Liga", "Acasa", "Deplasare", "xG-H", "xG-A",
                    "1", "X", "2", "O2.5", "BTTS", "Conf.",
                ],
                tablefmt="rounded_outline",
            )
        )

    def print_form(self):
        header("FORMA RECENTA (sintetica) & ACCIDENTARI")
        rows = []
        for m in self.match_analysis:
            fh = m["form_home"]
            fa = m["form_away"]
            rows.append([
                m["home_team"][:14],
                fh.get("form_str", "?")[:13],
                fh.get("streak", "?"),
                f"{fh.get('gf', 0):.1f}",
                m["away_team"][:14],
                fa.get("form_str", "?")[:13],
                fa.get("streak", "?"),
                f"{fa.get('gf', 0):.1f}",
                str(m["n_inj_home"]),
                str(m["n_inj_away"]),
            ])
        print(
            tabulate(
                rows,
                headers=[
                    "Acasa", "Forma-H", "Serie-H", "Gol/m-H",
                    "Dep.", "Forma-A", "Serie-A", "Gol/m-A",
                    "Inj-H", "Inj-A",
                ],
                tablefmt="rounded_outline",
            )
        )

    def print_tickets(self):
        header("CELE 3 BILETE (MODEL)")
        self.tickets = self.build_tickets()
        colors = [Fore.GREEN, Fore.YELLOW, Fore.RED]

        for i, t in enumerate(self.tickets):
            c = colors[i]
            print(f"\n{c}{'=' * 64}")
            print(f"{c}  {t['name']}")
            print(f"{c}  {t['style']}")
            print(f"{c}{'-' * 64}")
            rows = [
                [
                    p["league"][:16],
                    p["match"][:30],
                    p["kickoff"],
                    p["pick"],
                    f"{p['prob']:.0%}",
                    f"@{p['odds']:.2f}",
                    f"{p['conf']:.0f}%",
                ]
                for p in t["picks"]
            ]
            print(
                tabulate(
                    rows,
                    headers=["Liga", "Meci", "Ora", "Selectie", "Prob", "Cota(model)", "Conf."],
                    tablefmt="simple",
                )
            )
            print(
                f"{c}  Selectii:{len(t['picks'])}  Cota(model):{t['total_odds']:.2f}x  "
                f"Prob(model):{t['combined_prob']:.1f}%  |  50 RON -> {50 * t['total_odds']:.0f} RON"
            )
            print(f"{c}{'=' * 64}")

    def save_json(self):
        os.makedirs("output", exist_ok=True)
        path = f"output/analiza_model_{self.today}.json"
        data = {
            "generated_at": datetime.now().isoformat(),
            "date": self.today,
            "matches": [
                {
                    k: v
                    for k, v in m.items()
                    if k not in ["form_home", "form_away"]
                }
                for m in self.match_analysis
            ],
            "tickets": [
                {
                    "name": t["name"],
                    "picks": t["picks"],
                    "total_odds": t["total_odds"],
                    "combined_prob": t["combined_prob"],
                }
                for t in self.tickets
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        ok(f"Salvat: {path}")

    def run(self):
        print(
            Fore.CYAN
            + Style.BRIGHT
            + """
+--------------------------------------------------------------+
|   FLASHCORE ANALYZER (HTML) v3.0 -- Sursa: Flashscore HTML  |
|   by Costin Picu | Poisson + Forma + Accidentari (model)    |
+--------------------------------------------------------------+"""
        )

        matches = self.load_today_matches()
        if not matches:
            err("Niciun meci extras. Verifica URL-urile din LEAGUE_URLS.")
            return

        self.run_analysis(matches)
        if not self.match_analysis:
            err("Analiza esuata.")
            return

        self.print_analysis()
        self.print_form()
        self.print_tickets()
     #   self.save_json()

        print(
            f"\n{Fore.MAGENTA}  Pariatzi responsabil. Varsta minima: 18 ani. "
            f"Model statistic, NU garanteaza profit."
        )
        print(
            Fore.CYAN
            + f"  Done! JSON -> output/analiza_model_{self.today}.json\n"
        )


if __name__ == "__main__":
    import argparse
   

    p = argparse.ArgumentParser(
        description="Flashscore HTML Betting Analyzer (Model Poisson)"
    )
    p.add_argument(
        "--leagues",
        nargs="*",
        default=[
            "Romania",
            "Cupa mondiala"
        ],
        help=(
            "Ligi dorite, corespund cheilor din LEAGUE_URLS. "
            "Ex: --leagues Romania 'Premier League'"
        ),
    )

    # IMPORTANT: in Jupyter sys.argv contine si --f=..., deci folosim parse_known_args
    args, _ = p.parse_known_args()

    analyzer = BettingAnalyzer(leagues_filter=args.leagues or None)
    analyzer.run()

