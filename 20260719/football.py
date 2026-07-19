# -*- coding: utf-8 -*-
"""
Analizor pariuri fotbal (Flashscore) - modul backend pentru aplicatia web.

Adaptat din scriptul de consola "FOOTBALL BETTING ANALYZER" by Costin Picu.
Model: Poisson + Forma, calculat DOAR pe istoric REAL extras din Flashscore.
Daca un meci nu are istoric real pentru ambele echipe, NU este analizat
(nu se inventeaza date - fara istoric sintetic).

IMPORTANT despre extragerea meciurilor:
  - Extragerea reala din paginile Flashscore are nevoie de un browser automat
    (Playwright + Chromium). Acesta merge doar pe un server unde poti instala
    Chromium (ex: Render). Pe pplx.app browserul NU este disponibil, deci
    extragerea reala este oprita si interfata afiseaza un mesaj clar.
  - Cand Playwright lipseste, functia scrape_available() intoarce False, iar
    endpoint-ul de analiza raspunde elegant (fara sa crape aplicatia).

Configuratia de ligi (nume + URL) se salveaza pe disc in leagues.json,
deci este partajata (o vede oricine intra pe acelasi server).
"""

import os
import json
import threading
from datetime import datetime, timedelta

import numpy as np

# scipy este optional; daca lipseste, folosim o implementare proprie a PMF Poisson.
try:
    from scipy.stats import poisson as _sp_poisson

    def _poisson_pmf(k, mu):
        return float(_sp_poisson.pmf(k, mu))
except Exception:  # pragma: no cover
    import math

    def _poisson_pmf(k, mu):
        if mu <= 0:
            return 1.0 if k == 0 else 0.0
        return float(math.exp(-mu) * (mu ** k) / math.factorial(k))

from bs4 import BeautifulSoup


# ------------------------------------------------------------------
# CONFIG LIGI - persistate pe disc (partajat pe server)
# ------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
LEAGUES_PATH = os.path.join(_HERE, "leagues.json")
_LEAGUES_LOCK = threading.Lock()

# Valori default (exact cele din scriptul original)
DEFAULT_LEAGUES = [
    {"name": "Romania", "url": "https://www.flashscore.ro/fotbal/romania/superliga/"},
    {"name": "Cupa mondiala", "url": "https://www.flashscore.ro/fotbal/lume/cupa-mondiala/meciuri/"},
]
DEFAULT_DAYS_WINDOW = 3

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def load_leagues():
    """Citeste ligile din leagues.json; daca nu exista, scrie default-urile."""
    with _LEAGUES_LOCK:
        if not os.path.exists(LEAGUES_PATH):
            _write_leagues_unlocked(DEFAULT_LEAGUES)
            return [dict(x) for x in DEFAULT_LEAGUES]
        try:
            with open(LEAGUES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            leagues = data.get("leagues", [])
            # sanitizare minima
            clean = []
            for it in leagues:
                name = str(it.get("name", "")).strip()
                url = str(it.get("url", "")).strip()
                if name and url:
                    clean.append({"name": name, "url": url})
            return clean or [dict(x) for x in DEFAULT_LEAGUES]
        except Exception:
            return [dict(x) for x in DEFAULT_LEAGUES]


def _write_leagues_unlocked(leagues):
    with open(LEAGUES_PATH, "w", encoding="utf-8") as f:
        json.dump({"leagues": leagues}, f, ensure_ascii=False, indent=2)


def save_leagues(leagues):
    """Salveaza lista completa de ligi (suprascrie). Returneaza lista curatata."""
    clean = []
    seen = set()
    for it in leagues:
        name = str(it.get("name", "")).strip()
        url = str(it.get("url", "")).strip()
        if not name or not url:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append({"name": name, "url": url})
    with _LEAGUES_LOCK:
        _write_leagues_unlocked(clean)
    return clean


# ------------------------------------------------------------------
# Detectare Playwright (extragere reala) - optional
# ------------------------------------------------------------------
def scrape_available():
    """True doar daca putem folosi Playwright (browser automat) pe acest server."""
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except Exception:
        return False


# ------------------------------------------------------------------
# SCRAPER fixtures + istoric (Playwright + BeautifulSoup)
# ------------------------------------------------------------------
class FixturesScraper:
    """Extrage pagini Flashscore cu Chromium headless, OPTIMIZAT pentru viteza:
    - o SESIUNE de browser refolosita pentru toate paginile unei analize
      (lansarea Chromium e cel mai scump pas pe 0.1 CPU - o platim o singura data);
    - cookie-urile se accepta O SINGURA data per sesiune;
    - memoria ramane mica: tot un singur browser + o singura pagina.
    Fara sesiune deschisa, fiecare fetch isi porneste/inchide propriul browser
    (comportament vechi, folosit de ex. la diagnostic)."""

    # Flag-uri agresive de memorie/CPU: obligatorii pe Render 0.1 CPU / 512 MB.
    BROWSER_ARGS = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--single-process",
        "--no-zygote",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-client-side-phishing-detection",
        "--disable-default-apps",
        "--disable-sync",
        "--mute-audio",
        "--no-first-run",
        "--js-flags=--max-old-space-size=128",
    ]

    def __init__(self, timeout_s=25):
        self.timeout_s = timeout_s
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._cookies_done = False

    @staticmethod
    def _block_route(route):
        """Blocheaza resursele grele (imagini/fonturi/css/media) si reclamele -
        consuma cea mai multa memorie si timp; noua ne trebuie doar HTML+JS."""
        rt = route.request.resource_type
        if rt in ("image", "media", "font", "stylesheet"):
            try:
                route.abort()
                return
            except Exception:
                pass
        url_l = route.request.url.lower()
        if any(b in url_l for b in ("doubleclick", "googlesyndication",
                "google-analytics", "googletagmanager", "facebook",
                "/ads", "adservice", "scorecardresearch", "hotjar")):
            try:
                route.abort()
                return
            except Exception:
                pass
        try:
            route.continue_()
        except Exception:
            pass

    # ---------------- sesiune persistenta (viteza) ----------------
    def open_session(self):
        """Porneste UN singur Chromium refolosit pentru toate paginile."""
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True, args=self.BROWSER_ARGS)
        self._context = self._browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            locale="ro-RO",
            viewport={"width": 800, "height": 600},
            device_scale_factor=1,
            java_script_enabled=True,
        )
        try:
            self._context.route("**/*", self._block_route)
        except Exception:
            pass
        self._page = self._context.new_page()
        self._cookies_done = False

    def close_session(self):
        """Inchide complet browserul si elibereaza memoria."""
        for closer in (lambda: self._browser.close(), lambda: self._pw.stop()):
            try:
                closer()
            except Exception:
                pass
        self._pw = self._browser = self._context = self._page = None
        self._cookies_done = False

    def _restart_session(self):
        self.close_session()
        self.open_session()

    def _nav_and_get(self, url):
        """Navigheaza pagina sesiunii curente si intoarce HTML-ul."""
        page = self._page
        page.goto(url, timeout=self.timeout_s * 1000, wait_until="domcontentloaded")
        # Accepta bannerul de cookies DOAR o data per sesiune (economie mare de timp).
        if not self._cookies_done:
            for sel in (
                "#onetrust-accept-btn-handler",
                "button[aria-label='Sunt de acord']",
                "button:has-text('Sunt de acord')",
                "button:has-text('Accept')",
            ):
                try:
                    page.click(sel, timeout=2500)
                    page.wait_for_timeout(500)
                    break
                except Exception:
                    pass
            self._cookies_done = True
        # Asteapta randurile de meciuri (retry cu scroll)
        got = False
        for _ in range(2):
            try:
                page.wait_for_selector("[id^=g_]", timeout=8000)
                got = True
                break
            except Exception:
                try:
                    page.mouse.wheel(0, 2000)
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    page.wait_for_timeout(2000)
        if not got:
            page.wait_for_timeout(2500)
        page.wait_for_timeout(300)
        return page.content()

    def _fetch_html(self, url):
        """Ia HTML-ul unei pagini. Refoloseste sesiunea daca e deschisa;
        altfel porneste si inchide un browser doar pentru acest apel.
        La orice eroare de pagina/browser, reporneste sesiunea O data si reincearca."""
        ephemeral = self._page is None
        if ephemeral:
            self.open_session()
        try:
            try:
                return self._nav_and_get(url)
            except Exception:
                # browserul/pagina a picat (ex. --single-process e sensibil) ->
                # restart o singura data si reincercare
                self._restart_session()
                return self._nav_and_get(url)
        except Exception:
            return ""
        finally:
            if ephemeral:
                self.close_session()

    def debug_fetch(self, url):
        """Diagnostic: intoarce URL-ul incercat, marimea HTML, nr de randuri g_,
        titlul paginii si un fragment. Nu ridica exceptii."""
        target = self.fixtures_url(url)
        info = {"input_url": url, "fixtures_url": target, "error": None,
                "html_len": 0, "g_count": 0, "parsed": 0, "title": "", "snippet": ""}
        try:
            html = self._fetch_html(target)
            info["html_len"] = len(html)
            low = html.lower()
            info["g_count"] = low.count('id="g_')
            soup = BeautifulSoup(html, "html.parser")
            t = soup.find("title")
            info["title"] = t.get_text(strip=True) if t else ""
            info["parsed"] = len(self.parse_fixtures("?", html))
            info["snippet"] = (soup.get_text(" ", strip=True)[:300]) if soup else ""
        except Exception as e:
            info["error"] = f"{type(e).__name__}: {e}"
        return info

    @staticmethod
    def fixtures_url(url):
        """Asigura ca URL-ul ligii duce la pagina de meciuri viitoare (/meciuri/).
        Pagina default a unei ligi arata clasamentul/rezultatele, nu fixtures."""
        u = (url or "").strip()
        if not u:
            return u
        base = u.split("#")[0].rstrip("/")
        # daca deja pointeaza spre meciuri/rezultate/clasament, lasa asa
        low = base.lower()
        for kw in ("/meciuri", "/rezultate", "/clasament", "/program"):
            if low.endswith(kw):
                return base + "/"
        return base + "/meciuri/"

    def fetch_html(self, url):
        try:
            return self._fetch_html(url)
        except Exception:
            return ""

    def _parse_match_datetime(self, raw):
        if not raw:
            return None
        now = datetime.now()
        raw = raw.strip()
        for fmt in ["%d.%m.%Y %H:%M", "%d.%m.%Y"]:
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                pass
        for fmt in ["%d.%m. %H:%M", "%d.%m."]:
            try:
                dt = datetime.strptime(raw, fmt)
                return dt.replace(year=now.year)
            except ValueError:
                pass
        try:
            dt = datetime.strptime(raw, "%H:%M")
            return now.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
        except ValueError:
            pass
        return None

    @staticmethod
    def _team_url_from_slug(origin, slug):
        """Slug de forma '<nume>-<id8>' -> URL real de echipa
        '<origin>/echipa/<nume>/<id8>/'. Intoarce None daca nu poate separa id-ul."""
        if not slug or "-" not in slug:
            return None
        name, _, team_id = slug.rpartition("-")
        if not name or not team_id:
            return None
        return f"{origin}/echipa/{name}/{team_id}/"

    def parse_fixtures(self, league_name, html):
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        matches = []
        for div in soup.select("[id^=g_]"):
            home_el = div.select_one(".event__homeParticipant")
            away_el = div.select_one(".event__awayParticipant")
            time_el = div.select_one(".event__stageTime")
            row_link_el = div.select_one("a.eventRowLink")
            if not home_el or not away_el:
                continue
            home = home_el.get_text(strip=True)
            away = away_el.get_text(strip=True)
            if not home or not away:
                continue
            if not time_el:
                time_el = div.select_one(".event__time")
            time_txt = time_el.get_text(strip=True) if time_el else ""
            match_dt = None
            if time_el:
                # Flashscore pune ora in TEXT (ex '19.07. 16:30'); 'title' e adesea gol.
                match_dt = self._parse_match_datetime(time_txt) or self._parse_match_datetime(time_el.get("title"))
            home_url, away_url = None, None
            if row_link_el and row_link_el.has_attr("href"):
                # href e o pagina de MECI, ex:
                #   https://www.flashscore.ro/meci/fotbal/atletico-mg-hGLC5Bah/bahia-UeD7XtzM/?mid=...
                # Fiecare slug e <nume>-<id8>. Pagina REALA de echipa este
                #   https://www.flashscore.ro/echipa/<nume>/<id8>/
                # iar istoricul la .../rezultate/. Construim URL-ul real de echipa
                # ca sa luam ISTORIC REAL (nu date inventate).
                parts = row_link_el["href"].split("?")[0].rstrip("/").split("/")
                if len(parts) >= 2:
                    away_slug = parts[-1]
                    home_slug = parts[-2]
                    origin = "/".join(row_link_el["href"].split("/")[:3])  # https://host
                    home_url = self._team_url_from_slug(origin, home_slug)
                    away_url = self._team_url_from_slug(origin, away_slug)
            matches.append({
                "league": league_name,
                "home_team": home,
                "away_team": away,
                "kickoff": time_txt,
                "match_datetime": match_dt,
                "home_team_url": home_url,
                "away_team_url": away_url,
            })
        return matches

    def get_league_fixtures(self, league_name, url):
        target = self.fixtures_url(url)
        return self.parse_fixtures(league_name, self.fetch_html(target))


class TeamHistoryScraper:
    def __init__(self, timeout_s=25, fetcher=None):
        # fetcher partajat => REFOLOSESTE browserul deja pornit (viteza).
        self._f = fetcher or FixturesScraper(timeout_s=timeout_s)

    def fetch_team_history(self, team_name, team_url):
        if not team_url:
            return []
        url = team_url.rstrip("/") + "/rezultate/"
        return self.parse_team_history(team_name, self._f.fetch_html(url))

    def parse_team_history(self, team_name, html, max_matches=24):
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        history = []
        for div in soup.select("[id^=g_]")[:max_matches]:
            home_el = div.select_one(".event__homeParticipant")
            away_el = div.select_one(".event__awayParticipant")
            sh = div.select_one(".event__score--home")
            sa = div.select_one(".event__score--away")
            time_el = div.select_one(".event__stageTime")
            if not home_el or not away_el or not sh or not sa:
                continue
            try:
                hg = int(sh.get_text(strip=True))
                ag = int(sa.get_text(strip=True))
            except ValueError:
                continue
            # Data e de obicei in TEXT (ex '31.05. 19:00'); 'title' e adesea gol.
            raw_txt = time_el.get_text(strip=True) if time_el else None
            raw_title = time_el.get("title") if time_el else None
            match_date = (self._f._parse_match_datetime(raw_txt)
                          or self._f._parse_match_datetime(raw_title))
            if match_date is None:
                match_date = datetime.now()
            elif match_date > datetime.now() + timedelta(days=2):
                # istoric = trecut; '31.12.' parsat cu anul curent ar parea viitor -> anul precedent
                match_date = match_date.replace(year=match_date.year - 1)
            res = "H" if hg > ag else ("A" if ag > hg else "D")
            history.append({
                "date": match_date,
                "home_team": home_el.get_text(strip=True),
                "away_team": away_el.get_text(strip=True),
                "home_goals": hg,
                "away_goals": ag,
                "result": res,
            })
        return history


# ------------------------------------------------------------------
# MODEL POISSON + FORMA + ACCIDENTARI
# ------------------------------------------------------------------
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
        teams = sorted(set([m["home_team"] for m in matches] + [m["away_team"] for m in matches]))
        dates = [m["date"] for m in matches]
        weights = PoissonModel.time_weight(dates)
        attack, defense = {}, {}
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
            "attack": attack, "defense": defense,
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
        col_h = [_poisson_pmf(i, mu_h) for i in range(max_g + 1)]
        col_a = [_poisson_pmf(j, mu_a) for j in range(max_g + 1)]
        pm = np.outer(col_h, col_a)
        p1 = float(np.sum(np.tril(pm, -1)))
        px = float(np.sum(np.diag(pm)))
        p2 = float(np.sum(np.triu(pm, 1)))
        o25 = float(sum(pm[i][j] for i in range(max_g + 1) for j in range(max_g + 1) if i + j > 2))
        o15 = float(sum(pm[i][j] for i in range(max_g + 1) for j in range(max_g + 1) if i + j > 1))
        btts = float((1 - _poisson_pmf(0, mu_h)) * (1 - _poisson_pmf(0, mu_a)))
        return {
            "home_win": round(p1, 4), "draw": round(px, 4), "away_win": round(p2, 4),
            "over_25": round(o25, 4), "under_25": round(1 - o25, 4),
            "over_15": round(o15, 4), "btts_yes": round(btts, 4),
            "mu_home": round(mu_h, 2), "mu_away": round(mu_a, 2),
        }


def team_form(matches, team, n=6):
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
    sc, cnt = chars[0], 1
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


# ------------------------------------------------------------------
# ANALYZER
# ------------------------------------------------------------------
class BettingAnalyzer:
    def __init__(self, leagues, days_window=DEFAULT_DAYS_WINDOW):
        self.leagues = leagues
        self.days_window = int(days_window)
        self.scraper = FixturesScraper()
        # acelasi fetcher => un singur browser pentru fixtures + istoric
        self.history_scraper = TeamHistoryScraper(fetcher=self.scraper)
        self.match_analysis = []
        self.tickets = []
        # cache istoric per URL de echipa: o echipa care apare in mai multe
        # meciuri e descarcata O SINGURA data (viteza).
        self._hist_cache = {}

    def _team_history_cached(self, team, url):
        if url in self._hist_cache:
            return self._hist_cache[url]
        hist = self.history_scraper.fetch_team_history(team, url)
        self._hist_cache[url] = hist
        return hist

    def load_matches(self):
        all_matches = []
        for lg in self.leagues:
            all_matches.extend(self.scraper.get_league_fixtures(lg["name"], lg["url"]))
        now = datetime.now()
        cutoff = now + timedelta(days=self.days_window)
        filtered = []
        for m in all_matches:
            dt = m.get("match_datetime")
            if dt is None:
                filtered.append(m)
            elif now - timedelta(hours=2) <= dt <= cutoff:
                filtered.append(m)
        return filtered, len(all_matches)

    def analyze_match(self, match):
        """Analizeaza un meci FOLOSIND DOAR ISTORIC REAL.
        Daca nu exista istoric real pentru ambele echipe, intoarce None -
        NU inventam date (fara istoric sintetic)."""
        home, away = match["home_team"], match["away_team"]
        h_url, a_url = match.get("home_team_url"), match.get("away_team_url")
        # Fara URL de echipa nu putem lua istoric real -> nu analizam.
        if not h_url or not a_url:
            return None
        h_hist = self._team_history_cached(home, h_url)
        a_hist = self._team_history_cached(away, a_url)
        # Daca lipseste istoricul real al oricarei echipe -> nu analizam.
        if not h_hist or not a_hist:
            return None
        all_hist = sorted(h_hist + a_hist, key=lambda x: x["date"])
        model = PoissonModel.fit(all_hist)
        # Fit intoarce None daca sunt sub 5 meciuri reale -> date insuficiente.
        if model is None:
            return None
        mu_h, mu_a = PoissonModel.predict_goals(model, home, away)
        form_h = team_form(all_hist, home)
        form_a = team_form(all_hist, away)
        mu_h = max(0.2, mu_h * (1.0 + (form_h["pts"] - 9) * 0.01))
        mu_a = max(0.2, mu_a * (1.0 + (form_a["pts"] - 9) * 0.01))
        # Accidentarile nu sunt extrase real, deci NU le inventam (factor neutru).
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
        return {
            "match": f"{home} vs {away}", "home_team": home, "away_team": away,
            "league": match["league"], "kickoff": match["kickoff"],
            "mu_home": round(mu_h, 2), "mu_away": round(mu_a, 2), "probs": probs,
            "main_pick": main_pick, "main_prob": round(main_prob, 4), "main_odds": impl_odds(main_prob),
            "goals_pick": goals_pick, "goals_prob": round(goals_prob, 4), "goals_odds": impl_odds(goals_prob),
            "confidence": round(max_p * 100, 1),
            "form_home": form_h, "form_away": form_a,
            "used_synthetic_history": False,
            "n_hist_matches": model["n_matches"],
        }

    def run_analysis(self, matches):
        """Analizeaza doar meciurile cu istoric real. Cele fara date
        suficiente (analyze_match a intors None) sunt sarite si numarate."""
        self.skipped_no_data = 0
        for m in matches:
            try:
                res = self.analyze_match(m)
                if res is None:
                    self.skipped_no_data += 1
                    continue
                self.match_analysis.append(res)
            except Exception:
                self.skipped_no_data += 1

    def build_tickets(self):
        if not self.match_analysis:
            return []
        by_conf = sorted(self.match_analysis, key=lambda x: x["confidence"], reverse=True)
        by_odds = sorted(self.match_analysis, key=lambda x: x["main_odds"], reverse=True)
        by_goals = sorted(self.match_analysis, key=lambda x: x["goals_prob"], reverse=True)

        def mk(m, goals=False):
            return {
                "match": m["match"], "league": m["league"], "kickoff": m["kickoff"],
                "pick": m["goals_pick"] if goals else m["main_pick"],
                "prob": m["goals_prob"] if goals else m["main_prob"],
                "odds": m["goals_odds"] if goals else m["main_odds"],
                "conf": m["confidence"],
            }

        t1 = [mk(m) for m in by_conf[:4]]
        used = {m["match"] for m in by_conf[:3]}
        t2 = [mk(m) for m in by_conf[:3]] + [mk(m, True) for m in by_goals if m["match"] not in used][:2]
        vb = [mk(m) for m in self.match_analysis if m["main_odds"] >= 2.5]
        used_v = {p["match"] for p in vb}
        vb += [mk(m) for m in by_odds if m["match"] not in used_v and m["main_prob"] >= 0.30][:max(0, 4 - len(vb))]
        t3 = vb[:4]
        tickets = [
            {"name": "BILET 1 - SIGUR (model, cote implicite)", "style": "Top 4 selectii probabilitate maxima", "picks": t1},
            {"name": "BILET 2 - ECHILIBRAT (mix 1X2 + goluri)", "style": "Mix rezultate finale + goluri", "picks": t2},
            {"name": "BILET 3 - VALUE (model, cote mari)", "style": "Selectii cu cote mai mari (model), risc ridicat", "picks": t3},
        ]
        for t in tickets:
            op, cp = 1.0, 1.0
            for p in t["picks"]:
                op *= p["odds"]
                cp *= p["prob"]
            t["total_odds"] = round(op, 2)
            t["combined_prob"] = round(cp * 100, 2)
        return tickets

    def result(self, total_extracted, in_window_count=None):
        self.tickets = self.build_tickets()
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "days_window": self.days_window,
            "total_extracted": total_extracted,
            "in_window": in_window_count if in_window_count is not None else len(self.match_analysis),
            "analyzed": len(self.match_analysis),
            "skipped_no_data": getattr(self, "skipped_no_data", 0),
            "matches": [
                {k: v for k, v in m.items() if k not in ("form_home", "form_away")}
                | {"form_home": m["form_home"], "form_away": m["form_away"]}
                for m in self.match_analysis
            ],
            "tickets": self.tickets,
        }


def analyze(leagues, days_window=DEFAULT_DAYS_WINDOW):
    """Ruleaza analiza completa. Presupune ca scrape_available() este True."""
    az = BettingAnalyzer(leagues, days_window=days_window)
    # UN singur browser pentru toata analiza (fixtures + toate istoricele):
    # lansarea Chromium e cel mai scump pas pe CPU mic - o facem o data.
    az.scraper.open_session()
    try:
        matches, total = az.load_matches()
        in_window = len(matches)
        az.run_analysis(matches)
    finally:
        az.scraper.close_session()
    return az.result(total, in_window_count=in_window)
