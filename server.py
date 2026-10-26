#!/usr/bin/env python3
"""Flask backend for Simulator complet Loto.

Loto 6/49 uses the shared, deterministic uniform-coverage engine. Its default
package has six mutually disjoint base tickets and one extra number per ticket.
The full seven-source history is validated on every request; historical row
corrections do not change tickets within the latest-draw-date cycle.
Loto 5/40, Joker and football retain their separate existing engines.
"""

import hashlib
import json
import math
import time
from collections import Counter, defaultdict

import numpy as np
import requests
from flask import Flask, jsonify, request, send_from_directory
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os

import perplexitymix as coverage649
from loto_archive_parser import (
    ARCHIVE_URLS_649, LotoDataExtractionError, archive_request_url,
    extract_draws_from_html, normalize_draws,
)

app = Flask(__name__, static_folder=None)  # static files served explicitly below

# app.root_path = directorul in care sta server.py -> functioneaza indiferent
# din ce director e pornit procesul (gunicorn, python direct etc.).
@app.route("/ads.txt")
def ads_txt():
    return send_from_directory(app.root_path, "ads.txt")

@app.route("/robots.txt")
def robots_txt():
    # Necesar pt SEO + crawlerul AdSense (Mediapartners-Google).
    return send_from_directory(app.root_path, "robots.txt")

@app.route("/sitemap.xml")
def sitemap_xml():
    return send_from_directory(app.root_path, "sitemap.xml")

ARCHIVE_URLS = ARCHIVE_URLS_649
SEED = 649
TIMEOUT = 30

# cache simplu in memorie: (timestamp, draws)
_CACHE = {"ts": 0, "draws": None, "meta": None}
_CACHE_TTL = 0  # Always validate the complete live 6/49 history, as on the Site.


# ---------------- HTTP + parsare ----------------
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
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    })
    return s


def download_all():
    """Load all seven identified 6/49 archives, or fail without partial history."""
    rows, per_source = [], {}
    cache_buster = time.time_ns()
    with build_session() as session:
        for url in ARCHIVE_URLS:
            try:
                response = session.get(archive_request_url(url, cache_buster), timeout=TIMEOUT)
                response.raise_for_status()
                parsed = extract_draws_from_html(response.text, response.url)
            except requests.RequestException as exc:
                raise LotoDataExtractionError(
                    f"Arhiva indisponibila: {url}. Generarea cu istoric partial este oprita."
                ) from exc
            if not parsed:
                raise LotoDataExtractionError(f"Nu s-au extras date Loto 6/49 valide din {url}.")
            per_source[url] = len(parsed)
            rows.extend(parsed)
    normalized = normalize_draws(rows)
    if len(normalized) < 30:
        raise LotoDataExtractionError("Istoric 6/49 insuficient pentru generare.")
    meta = {
        "total_draws": len(normalized),
        "first_date": normalized[0]["date"],
        "last_date": normalized[-1]["date"],
        "per_source": per_source,
    }
    return normalized, meta


def get_draws(force=False):
    now = time.time()
    if (not force) and _CACHE["draws"] and (now - _CACHE["ts"] < _CACHE_TTL):
        return _CACHE["draws"], _CACHE["meta"], True
    draws, meta = download_all()
    if not draws:
        raise RuntimeError("Nu s-a putut extrage nicio extragere valida din arhiva.")
    _CACHE.update(ts=now, draws=draws, meta=meta)
    return draws, meta, False


# ---------------- API Loto 6/49 ----------------
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _strict_integer(params, name, fallback, minimum, maximum):
    raw = params.get(name)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return fallback
    try:
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            raise ValueError
        value = float(raw)
        if not math.isfinite(value) or not value.is_integer() or not minimum <= value <= maximum:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"Parametrul {name} trebuie sa fie un numar intreg intre {minimum} si {maximum}.") from None
    return int(value)


@app.route("/api/predict", methods=["GET", "POST"])
def predict():
    """Generate free 6/49 tickets with the same v2 engine and policy as the Site."""
    params = request.args
    if request.method == "POST":
        params = request.get_json(silent=True)
        if not isinstance(params, dict):
            return jsonify({"error": "Cererea de generare nu este valida."}), 422
    try:
        n_bilete = _strict_integer(params, "n", 6, 1, 50)
        seed = _strict_integer(params, "seed", SEED, 0, 4294967295)
        extra9 = _strict_integer(params, "extra9", 1, 0, 10)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    try:
        draws, meta, from_cache = get_draws(force=params.get("force") == "1")
        # Normalize even an injected/cached history before its data reaches the
        # shared engine. Joker markers and future/invalid dates are rejected.
        draws = normalize_draws(draws)
        if len(draws) < 30:
            raise LotoDataExtractionError("Istoric 6/49 insuficient pentru generare.")
        stats = coverage649.analyse649(draws)
        generated = coverage649.generate649(stats, n=n_bilete, seed=seed, extra9=extra9)
    except LotoDataExtractionError as exc:
        return jsonify({"error": str(exc)}), 502
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 502
    history_hash = hashlib.sha256(json.dumps(draws, separators=(",", ":")).encode()).hexdigest()
    data = {**meta, "total_draws": len(draws), "first_date": draws[0]["date"],
            "last_date": draws[-1]["date"], "latest_draw": draws[-1],
            "history_hash": history_hash, "source": "loto49.ro"}
    generation = generated["generation"]
    return jsonify({
        "engine": coverage649.ENGINE_VERSION,
        "model": {"frequencyInfluence": 0, "pairInfluence": 0, "delayInfluence": 0, "filter": False},
        "calibration": stats["config"], "generation": generation,
        "coverage": generated["coverage"], "seed": seed, "from_cache": from_cache,
        "data": data, "target_profile": stats["target_profile"],
        "top_numbers": stats["top_numbers"], "top_pairs": stats["top_pairs"],
        "tickets6": generated["tickets"], "tickets9": generated["extensions"],
        "params": {
            "n": n_bilete, "seed": seed, "pool": 49, "pool9": 49, "extra9": extra9,
            "extension_size": 6 + extra9, "extension_variants": math.comb(6 + extra9, 6),
            "mode": "unified", "generation_mode": generation["mode"],
            "cycle_date": generation["cycle_date"], "disjoint_prefix": generation["disjoint_prefix"],
            "filter": False, "w_delay": 0, "frequency_influence": 0,
            "pair_influence": 0, "max_overlap": 2,
        },
    })


# ==================== LOTO 5/40 + JOKER (motoare adaugate) ====================
try:
    import loto_multi as _lm
except Exception:
    _lm = None


@app.route("/api/predict540")
def predict_540():
    """Predictie Loto 5/40 (aceeasi metoda ca 6/49, adaptata la 1..40 / bilet 5 nr)."""
    if _lm is None:
        return jsonify({"error": "Modulul loto_multi nu este disponibil."}), 500

    n_bilete = _lm._clamp(int(request.args.get("n", 4)), 1, 50)
    pool = _lm._clamp(int(request.args.get("pool", 21)), 5, 40)
    pool9 = _lm._clamp(int(request.args.get("pool9", 21)), 5, 40)
    extra9 = _lm._clamp(int(request.args.get("extra9", 1)), 1, 10)
    seed = int(request.args.get("seed", _lm.SEED_540))
    use_filter = request.args.get("filter", "1") != "0"
    force = request.args.get("force", "0") == "1"

    w_freq = max(0.0, float(request.args.get("wfreq", 0.4)))
    w_recent = max(0.0, float(request.args.get("wrecent", 0.3)))
    w_delay = max(0.0, float(request.args.get("wdelay", 0.3)))
    w_pair = max(0.0, float(request.args.get("wpair", 0.3)))
    w_freq, w_recent, w_delay, w_pair = _lm.normalize_weights(w_freq, w_recent, w_delay, w_pair)

    if extra9 > pool9 - 5:
        extra9 = max(1, pool9 - 5)

    try:
        draws, meta, from_cache = _lm.get_draws_540(force=force)
    except Exception as e:
        return jsonify({"error": str(e)}), 422

    an = _lm.Analyzer540(draws)
    an.compute_frequency()
    an.compute_delay()
    an.compute_pairs()
    an.compute_transition()
    an.compute_profile_stats()
    an.compute_score(w_frequency=w_freq, w_recent_frequency=w_recent, w_delay=w_delay, w_pair=w_pair)

    rng = np.random.RandomState(seed)
    target, tickets5 = an.gen_tickets(rng, count=n_bilete, pool=pool, use_filter=use_filter)
    tickets9 = [an.extend_to_9(rng, t, pool=pool9, extra=extra9) for t in tickets5]

    top_numbers = sorted(an.score.items(), key=lambda x: -x[1])[:12]
    top_numbers = [{"n": n, "score": round(s, 4), "freq": an.frequency[n], "delay": an.delay[n]}
                   for n, s in top_numbers]
    top_pairs = [{"a": a, "b": b, "count": c} for (a, b), c in an.pair_counts.most_common(8)]

    def with_profile(tk):
        p = _lm.draw_profile_540(tk)
        return {"nums": tk, "sum": p["sum"], "ip": f'{p["odd"]}/{p["even"]}', "shape": p["shape"]}

    shapes = ["low-heavy", "balanced", "high-heavy"]
    transition = {}
    for a in shapes:
        row = an.transition.get(a, Counter())
        tot = sum(row.values()) or 1
        transition[a] = {b: round(row.get(b, 0) / tot, 3) for b in shapes}

    return jsonify({
        "seed": seed,
        "params": {
            "n": n_bilete, "pool": pool, "pool9": pool9, "extra9": extra9,
            "filter": use_filter,
            "w_freq": round(w_freq, 3), "w_recent": round(w_recent, 3),
            "w_delay": round(w_delay, 3), "w_pair": round(w_pair, 3),
        },
        "from_cache": from_cache,
        "data": meta,
        "profile_stats": {k: {kk: round(vv, 3) for kk, vv in v.items()}
                          for k, v in an.profile_stats.items()},
        "target_profile": {
            "sum": target["sum"], "sum_p": round(an.profile_stats["sum"][target["sum"]], 3),
            "max": target["max"], "max_p": round(an.profile_stats["max"][target["max"]], 3),
            "shape": target["shape"], "shape_p": round(an.profile_stats["shape"][target["shape"]], 3),
            "ip": target["ip"], "ip_p": round(an.profile_stats["ip"][target["ip"]], 3),
        },
        "transition": transition,
        "top_numbers": top_numbers,
        "top_pairs": top_pairs,
        "tickets5": [with_profile(t) for t in tickets5],
        "tickets9": tickets9,
    })


@app.route("/api/predictjoker")
def predict_joker():
    """Predictie Joker (5/45 + numar Joker 1/20)."""
    if _lm is None:
        return jsonify({"error": "Modulul loto_multi nu este disponibil."}), 500

    n_bilete = _lm._clamp(int(request.args.get("n", 2)), 1, 50)
    pool_main = _lm._clamp(int(request.args.get("pool", 21)), 5, 45)
    pool_main9 = _lm._clamp(int(request.args.get("pool9", 21)), 5, 45)
    extra_main9 = _lm._clamp(int(request.args.get("extra9", 1)), 1, 10)
    pool_joker = _lm._clamp(int(request.args.get("pooljoker", 8)), 1, 20)
    seed = int(request.args.get("seed", _lm.SEED_JOKER))
    use_filter = request.args.get("filter", "1") != "0"
    force = request.args.get("force", "0") == "1"

    w_freq = max(0.0, float(request.args.get("wfreq", 0.4)))
    w_delay = max(0.0, float(request.args.get("wdelay", 0.3)))
    w_pair = max(0.0, float(request.args.get("wpair", 0.3)))
    w_freq, w_delay, w_pair = _lm.normalize_weights(w_freq, w_delay, w_pair)

    wj_freq = max(0.0, float(request.args.get("wjfreq", 0.4)))
    wj_delay = max(0.0, float(request.args.get("wjdelay", 0.3)))
    wj_freq, wj_delay = _lm.normalize_weights(wj_freq, wj_delay)

    if extra_main9 > pool_main9 - 5:
        extra_main9 = max(1, pool_main9 - 5)

    try:
        draws, jokers, meta, from_cache = _lm.get_draws_joker(force=force)
    except Exception as e:
        return jsonify({"error": str(e)}), 422

    an = _lm.AnalyzerJoker(draws, jokers)
    an.compute_frequency()
    an.compute_delay()
    an.compute_pairs()
    an.compute_transition()
    an.compute_profile_stats()
    an.compute_score(w_freq=w_freq, w_delay=w_delay, w_pair=w_pair)
    an.compute_joker_frequency()
    an.compute_joker_delay()
    an.compute_joker_score(w_freq=wj_freq, w_delay=wj_delay)

    rng = np.random.RandomState(seed)
    target, tickets = an.gen_tickets(rng, count=n_bilete, pool_main=pool_main,
                                      pool_joker=pool_joker, use_filter=use_filter)
    tickets9 = [
        {"main9": an.extend_main_to_9(rng, main_t, pool=pool_main9, extra=extra_main9), "joker": j}
        for main_t, j in tickets
    ]

    top_numbers = sorted(an.score.items(), key=lambda x: -x[1])[:12]
    top_numbers = [{"n": n, "score": round(s, 4), "freq": an.frequency[n], "delay": an.delay[n]}
                   for n, s in top_numbers]
    top_joker_numbers = sorted(an.j_score.items(), key=lambda x: -x[1])[:8]
    top_joker_numbers = [{"n": n, "score": round(s, 4), "freq": an.j_frequency[n], "delay": an.j_delay[n]}
                         for n, s in top_joker_numbers]
    top_pairs = [{"a": a, "b": b, "count": c} for (a, b), c in an.pair_counts.most_common(8)]

    def with_profile(main_t, j):
        p = _lm.draw_profile_joker(main_t)
        return {"nums": list(main_t), "joker": j, "sum": p["sum"],
                "ip": f'{p["odd"]}/{p["even"]}', "shape": p["shape"]}

    shapes = ["low-heavy", "balanced", "high-heavy"]
    transition = {}
    for a in shapes:
        row = an.transition.get(a, Counter())
        tot = sum(row.values()) or 1
        transition[a] = {b: round(row.get(b, 0) / tot, 3) for b in shapes}

    return jsonify({
        "seed": seed,
        "params": {
            "n": n_bilete, "pool": pool_main, "pool9": pool_main9, "extra9": extra_main9,
            "pooljoker": pool_joker, "filter": use_filter,
            "w_freq": round(w_freq, 3), "w_delay": round(w_delay, 3), "w_pair": round(w_pair, 3),
            "wj_freq": round(wj_freq, 3), "wj_delay": round(wj_delay, 3),
        },
        "from_cache": from_cache,
        "data": meta,
        "profile_stats": {k: {kk: round(vv, 3) for kk, vv in v.items()}
                          for k, v in an.profile_stats.items()},
        "target_profile": {
            "sum": target["sum"], "sum_p": round(an.profile_stats["sum"][target["sum"]], 3),
            "max": target["max"], "max_p": round(an.profile_stats["max"][target["max"]], 3),
            "shape": target["shape"], "shape_p": round(an.profile_stats["shape"][target["shape"]], 3),
            "ip": target["ip"], "ip_p": round(an.profile_stats["ip"][target["ip"]], 3),
        },
        "transition": transition,
        "top_numbers": top_numbers,
        "top_joker_numbers": top_joker_numbers,
        "top_pairs": top_pairs,
        "tickets5": [with_profile(t, j) for t, j in tickets],
        "tickets9": tickets9,
    })


# ==================== FOOTBALL (analizor pariuri) ====================
try:
    import football as _fb
except Exception:
    _fb = None


@app.route("/api/football/leagues", methods=["GET"])
def fb_get_leagues():
    """Ligile curente + plaja de zile default + daca extragerea reala e disponibila."""
    if _fb is None:
        return jsonify({"error": "Modulul football nu este disponibil."}), 500
    return jsonify({
        "leagues": _fb.load_leagues(),
        "default_days_window": _fb.DEFAULT_DAYS_WINDOW,
        "scrape_available": _fb.scrape_available(),
    })


@app.route("/api/football/leagues", methods=["POST"])
def fb_save_leagues():
    """Salveaza (suprascrie) lista de ligi pe server. Body: {\"leagues\":[{name,url},...]}."""
    if _fb is None:
        return jsonify({"error": "Modulul football nu este disponibil."}), 500
    body = request.get_json(silent=True) or {}
    leagues = body.get("leagues", [])
    if not isinstance(leagues, list):
        return jsonify({"error": "Format invalid: 'leagues' trebuie sa fie o lista."}), 400
    saved = _fb.save_leagues(leagues)
    return jsonify({"leagues": saved, "saved": True})


@app.route("/api/football/analyze", methods=["POST"])
def fb_analyze():
    """Ruleaza analiza. Body optional: {\"days_window\":3, \"leagues\":[...]}.
    Daca 'leagues' lipseste, foloseste ligile salvate pe server.
    Daca extragerea reala (Playwright) nu e disponibila, intoarce 503 cu mesaj clar."""
    if _fb is None:
        return jsonify({"error": "Modulul football nu este disponibil."}), 500
    body = request.get_json(silent=True) or {}
    try:
        days = int(body.get("days_window", _fb.DEFAULT_DAYS_WINDOW))
    except Exception:
        days = _fb.DEFAULT_DAYS_WINDOW
    days = max(1, min(days, 14))
    leagues = body.get("leagues")
    if not isinstance(leagues, list) or not leagues:
        leagues = _fb.load_leagues()

    if not _fb.scrape_available():
        return jsonify({
            "error": "scrape_unavailable",
            "message": (
                "Extragerea meciurilor nu este disponibila pe acest server: lipseste "
                "modulul Python 'requests'. Instaleaza dependintele din requirements.txt."
            ),
            "scrape_available": False,
            "days_window": days,
            "leagues": leagues,
        }), 503

    try:
        data = _fb.analyze(leagues, days_window=days)
        data["scrape_available"] = True
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": "analyze_failed", "message": str(e)}), 500


@app.route("/api/football/debug", methods=["POST", "GET"])
def fb_debug():
    """Diagnostic: arata exact ce vede serverul cand incearca extragerea.
    Body optional: {\"leagues\":[...]}. Fara body, foloseste ligile salvate."""
    if _fb is None:
        return jsonify({"error": "Modulul football nu este disponibil."}), 500
    body = request.get_json(silent=True) or {}
    leagues = body.get("leagues")
    if not isinstance(leagues, list) or not leagues:
        leagues = _fb.load_leagues()
    out = {
        "scrape_available": bool(_fb.scrape_available()),
        "browser_fallback_available": bool(_fb.browser_available()),
        "feed_inline": [],
        "playwright_import": None,
        "chromium_launch": None,
        "leagues": [],
    }
    # 0) calea RAPIDA (feed inline, fara browser) - cea folosita in mod normal
    try:
        fx = _fb.FixturesScraper()
        for lg in leagues:
            try:
                target = fx.fixtures_url(lg.get("url") or "")
                fast = fx._feed.fixtures_from_feed(lg.get("name"), target)
                out["feed_inline"].append({
                    "name": lg.get("name"),
                    "feed_gasit": fast is not None,
                    "meciuri": len(fast) if fast is not None else 0,
                })
            except Exception as e:
                out["feed_inline"].append({"name": lg.get("name"), "error": f"{type(e).__name__}: {e}"})
    except Exception as e:
        out["feed_inline"] = f"{type(e).__name__}: {e}"
    # test import + launch separat, ca sa stim exact unde pica
    try:
        from playwright.sync_api import sync_playwright  # noqa
        out["playwright_import"] = "ok"
    except Exception as e:
        out["playwright_import"] = f"{type(e).__name__}: {e}"
    if out["playwright_import"] == "ok":
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                b = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
                b.close()
            out["chromium_launch"] = "ok"
        except Exception as e:
            out["chromium_launch"] = f"{type(e).__name__}: {e}"
    if out.get("chromium_launch") == "ok":
        sc = _fb.FixturesScraper()
        for lg in leagues:
            try:
                out["leagues"].append({"name": lg.get("name"), **sc.debug_fetch(lg.get("url"))})
            except Exception as e:
                out["leagues"].append({"name": lg.get("name"), "error": f"{type(e).__name__}: {e}"})
    return jsonify(out)


@app.route("/football.py")
def _hide_football_source():
    # Nu servim codul sursa ca fisier static.
    return ("Not found", 404)


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/schemes.js")
def schemes_js():
    return send_from_directory(".", "schemes.js", mimetype="application/javascript")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
