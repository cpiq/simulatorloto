#!/usr/bin/env python3
"""
server.py - backend pentru Simulator complet Loto 6/49.

Expune /api/predict care reproduce EXACT motorul combinat din scriptul
utilizatorului: frecventa + intarziere + perechi (scor combinat),
profilul extragerii (suma, max, I/P, forma), matrice Markov pe forma,
generare 12 bilete cu seed FIX reproductibil si extindere la 9 numere.

Istoricul se DESCARCA LIVE de pe loto49.ro la fiecare cerere (cu un cache
scurt in memorie ca sa nu lovim site-ul de prea multe ori).

ONEST: 6/49 e proces independent si uniform. Metoda NU creste sansa reala
de castig - face doar biletele sa arate realist statistic.
"""

import re
import time
from collections import Counter, defaultdict

import numpy as np
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os

try:
    import stripe  # plata; optional - daca lipseste, butonul ramane gratuit
except Exception:
    stripe = None

app = Flask(__name__, static_folder=None)  # static files served explicitly below
app.route("/robots.txt")
def robots_txt():
    return send_from_directory(app.root_path, "robots.txt")

@app.route("/sitemap.xml")
def sitemap_xml():
    return send_from_directory(app.root_path, "sitemap.xml")
# ---------------- Config plata (din variabile de mediu) ----------------
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "").strip()
PRICE_PER_PACK_LEI = float(os.environ.get("PRICE_PER_PACK_LEI", "5"))
FREE_TICKETS = int(os.environ.get("FREE_TICKETS", "3"))
TICKETS_PER_PACK = int(os.environ.get("TICKETS_PER_PACK", "3"))

if stripe and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Kill-switch explicit pentru plata. Implicit OPRIT (aplicatia e gratuita).
# Ca sa REACTIVEZI plata: seteaza in mediu PAYMENTS_ENABLED=true (sau 1/yes/on)
# SI cheile Stripe (STRIPE_SECRET_KEY + STRIPE_PUBLISHABLE_KEY).
PAYMENTS_ENABLED = os.environ.get("PAYMENTS_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")

# Plata e activa doar daca: kill-switch pornit SI biblioteca stripe instalata SI ambele chei prezente.
PAYMENTS_ON = bool(PAYMENTS_ENABLED and stripe and STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY)


def _public_base():
    """Adresa publica a aplicatiei pentru redirect dupa plata."""
    env = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if env:
        return env
    # deducem din request daca nu e setata explicit
    return request.host_url.rstrip("/")


def compute_price(n_bilete):
    """Primele FREE_TICKETS gratis; restul in pachete de TICKETS_PER_PACK,
    rotunjit IN SUS, la PRICE_PER_PACK_LEI lei pachetul."""
    import math
    n = max(0, int(n_bilete))
    paid = max(0, n - FREE_TICKETS)
    packs = math.ceil(paid / TICKETS_PER_PACK) if paid > 0 else 0
    total_lei = packs * PRICE_PER_PACK_LEI
    return {
        "n": n, "free": min(n, FREE_TICKETS), "paid_tickets": paid,
        "packs": packs, "total_lei": total_lei,
        "total_bani": int(round(total_lei * 100)),  # Stripe lucreaza in bani (subunitate)
    }
@app.route("/ads.txt")
def ads_txt():
    return send_from_directory(".", "ads.txt")   # daca e in radacina repo-ului
    # sau: return send_from_directory("static", "ads.txt")

ARCHIVE_URLS = (
    "https://www.loto49.ro/arhiva-loto49-1993-2000.php",
    "https://www.loto49.ro/arhiva-loto49-2001-2005.php",
    "https://www.loto49.ro/arhiva-loto49-2006-2010.php",
    "https://www.loto49.ro/arhiva-loto49-2011-2015.php",
    "https://www.loto49.ro/arhiva-loto-6-49-din-perioada-2016-2019.php",
    "https://www.loto49.ro/arhiva-loto-6-49-din-perioada-2020-2023.php",
    "https://www.loto49.ro/arhiva-loto49.php",
)
SEED = 649
TIMEOUT = 30

# cache simplu in memorie: (timestamp, draws)
_CACHE = {"ts": 0, "draws": None, "meta": None}
_CACHE_TTL = 1800  # 30 minute


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
    })
    return s


_PAT = re.compile(
    r"(?<!\d)(\d{4}-\d{2}-\d{2})"
    r"\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})"
    r"\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})(?!\d)"
)


def valid_draw(nums):
    return len(nums) == 6 and len(set(nums)) == 6 and all(1 <= n <= 49 for n in nums)


def parse_text(text):
    rows = []
    for m in _PAT.findall(text):
        nums = list(map(int, m[1:]))
        if valid_draw(nums):
            rows.append((m[0], nums))
    return rows


def download_all():
    session = build_session()
    seen = {}
    per_source = {}
    for url in ARCHIVE_URLS:
        try:
            r = session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            rows = parse_text(soup.get_text("\n", strip=True))
            if not rows:
                rows = parse_text(soup.get_text(" ", strip=True))
            per_source[url] = len(rows)
            for d, nums in rows:
                seen[(d, tuple(nums))] = (d, nums)
        except Exception as e:
            per_source[url] = f"err: {e}"
        time.sleep(0.4)
    items = list(seen.values())
    items.sort(key=lambda x: x[0])  # by date string YYYY-MM-DD
    draws = [nums for _, nums in items]
    meta = {
        "total_draws": len(draws),
        "first_date": items[0][0] if items else None,
        "last_date": items[-1][0] if items else None,
        "per_source": per_source,
    }
    return draws, meta


def get_draws(force=False):
    now = time.time()
    if (not force) and _CACHE["draws"] and (now - _CACHE["ts"] < _CACHE_TTL):
        return _CACHE["draws"], _CACHE["meta"], True
    draws, meta = download_all()
    if not draws:
        raise RuntimeError("Nu s-a putut extrage nicio extragere valida din arhiva.")
    _CACHE.update(ts=now, draws=draws, meta=meta)
    return draws, meta, False


# ---------------- profil extragere ----------------
def draw_profile(nums):
    s = sorted(nums)
    odd = sum(1 for n in s if n % 2 == 1)
    even = 6 - odd
    total = sum(s)
    mx = max(s)
    low = sum(1 for n in s if n <= 24)
    high = 6 - low
    if high - low >= 2:
        shape = "high-heavy"
    elif low - high >= 2:
        shape = "low-heavy"
    else:
        shape = "balanced"
    return {"sum": total, "max": mx, "odd": odd, "even": even, "shape": shape}


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


# ---------------- motor combinat ----------------
class Analyzer:
    def __init__(self, draws):
        self.draws = draws
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

    def compute_score(self, w_freq=0.4, w_delay=0.3, w_pair=0.3):
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
            self.score[n] = float(w_freq * fn[n - 1] + w_delay * dn[n - 1] + w_pair * pn[n - 1])

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

    def gen_ticket(self, rng, size=6, pool=20):
        ranked = sorted(self.score.items(), key=lambda x: x[1], reverse=True)[:pool]
        nums = np.array([n for n, _ in ranked])
        w = np.array([s for _, s in ranked], float)
        w = np.ones_like(w) / len(w) if w.sum() == 0 else w / w.sum()
        return sorted(rng.choice(nums, size, replace=False, p=w).tolist())

    def gen_tickets(self, rng, count=12, size=6, pool=20, use_filter=True):
        target = self.target_profile()
        seen = set()
        ordered = []
        attempts = 0
        max_attempts = count * 2000
        while len(ordered) < count and attempts < max_attempts:
            t = tuple(self.gen_ticket(rng, size, pool))
            attempts += 1
            if use_filter and not self.plausible(t, target):
                continue
            if t not in seen:
                seen.add(t)
                ordered.append(t)
        while len(ordered) < count:
            t = tuple(self.gen_ticket(rng, size, pool))
            if t not in seen:
                seen.add(t)
                ordered.append(t)
        return target, [list(t) for t in ordered]

    def extend_to_9(self, rng, ticket6, pool=22, extra=3):
        ranked = sorted(self.score.items(), key=lambda x: x[1], reverse=True)[:pool]
        existing = set(ticket6)
        nums = np.array([n for n, _ in ranked if n not in existing])
        w = np.array([s for n, s in ranked if n not in existing], float)
        if len(nums) < extra:
            # completam din toate numerele daca pool prea mic
            rest = [n for n in range(1, 50) if n not in existing and n not in nums.tolist()]
            nums = np.array(nums.tolist() + rest)
            w = np.array(w.tolist() + [0.0] * len(rest))
        w = np.ones_like(w) / len(w) if w.sum() == 0 else w / w.sum()
        extra_nums = rng.choice(nums, extra, replace=False, p=w)
        return sorted(existing.union(extra_nums.tolist()))


# ---------------- API plata Stripe ----------------
# Retinem sesiunile platite si deja folosite, ca un token sa nu fie refolosit.
_PAID_SESSIONS = {}   # session_id -> {"n": int, "used": bool}


@app.route("/api/config")
def api_config():
    """Frontend afla daca plata e activa, cheia publica si regulile de pret."""
    return jsonify({
        "payments_on": PAYMENTS_ON,
        "publishable_key": STRIPE_PUBLISHABLE_KEY if PAYMENTS_ON else "",
        "free_tickets": FREE_TICKETS,
        "tickets_per_pack": TICKETS_PER_PACK,
        "price_per_pack_lei": PRICE_PER_PACK_LEI,
        "currency": "ron",
    })


@app.route("/api/price")
def api_price():
    """Calcul de pret pentru un numar de bilete (informativ, pentru afisare)."""
    n = _clamp(int(request.args.get("n", 12)), 1, 50)
    return jsonify(compute_price(n))


@app.route("/api/create-checkout", methods=["POST"])
def create_checkout():
    """Creeaza o sesiune Stripe Checkout pentru biletele platite."""
    if not PAYMENTS_ON:
        return jsonify({"error": "Plata nu este configurata pe server."}), 503
    data = request.get_json(silent=True) or {}
    n = _clamp(int(data.get("n", 12)), 1, 50)
    pr = compute_price(n)
    if pr["total_bani"] <= 0:
        return jsonify({"error": "Acest numar de bilete este gratuit.", "price": pr}), 400
    base = _public_base()
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "ron",
                    "product_data": {
                        "name": "Predictie Loto 6/49 - %d bilete" % n,
                        "description": "%d bilete gratis + %d platite (%d pachete x %g lei)" % (
                            pr["free"], pr["paid_tickets"], pr["packs"], PRICE_PER_PACK_LEI),
                    },
                    "unit_amount": pr["total_bani"],
                },
                "quantity": 1,
            }],
            success_url=base + "/?paid_session={CHECKOUT_SESSION_ID}",
            cancel_url=base + "/?pay_cancel=1",
            metadata={"n": str(n)},
        )
    except Exception as e:
        return jsonify({"error": "Stripe: " + str(e)}), 502
    _PAID_SESSIONS[session.id] = {"n": n, "used": False, "confirmed": False}
    return jsonify({"checkout_url": session.url, "session_id": session.id, "price": pr})


def _session_is_paid(session_id):
    """Verifica la Stripe daca sesiunea a fost platita. Returneaza (ok, n)."""
    if not PAYMENTS_ON or not session_id:
        return False, 0
    try:
        s = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        return False, 0
    # Atentie: obiectul Stripe NU se comporta ca un dict obisnuit (.get poate
    # ridica AttributeError). Accesam campurile prin atribut, cu fallback.
    pay_status = getattr(s, "payment_status", None)
    if pay_status == "paid":
        meta = getattr(s, "metadata", None)
        # Pe obiectul Stripe, atat sesiunea cat si metadata se citesc prin
        # atribut (.get poate ridica AttributeError). Folosim getattr.
        raw_n = getattr(meta, "n", None) if meta is not None else None
        try:
            n = int(raw_n) if raw_n is not None else 0
        except Exception:
            n = 0
        return True, n
    return False, 0


@app.route("/api/verify")
def verify_payment():
    """Frontend verifica dupa redirect ca plata e confirmata."""
    sid = request.args.get("session_id", "")
    ok, n = _session_is_paid(sid)
    if ok:
        rec = _PAID_SESSIONS.get(sid, {"n": n, "used": False})
        rec["confirmed"] = True
        rec["n"] = n or rec.get("n", 0)
        _PAID_SESSIONS[sid] = rec
        return jsonify({"paid": True, "n": rec["n"], "used": rec.get("used", False)})
    return jsonify({"paid": False})


# ---------------- API ----------------
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


@app.route("/api/predict")
def predict():
    # parametri reglabili din interfata (cu limite de siguranta)
    n_bilete = _clamp(int(request.args.get("n", 12)), 1, 50)

    # --- Garda de plata pe server (nu se poate ocoli din browser) ---
    # Daca plata e activa si se cer mai multe bilete decat cele gratuite,
    # cererea trebuie sa vina cu un token de sesiune Stripe platita, valabil
    # pentru cel putin n_bilete si nefolosit inca.
    if PAYMENTS_ON and n_bilete > FREE_TICKETS:
        sid = request.args.get("pay", "")
        rec = _PAID_SESSIONS.get(sid)
        ok = False
        if rec and not rec.get("used"):
            paid_ok, paid_n = _session_is_paid(sid)
            if paid_ok and paid_n >= n_bilete:
                ok = True
        if not ok:
            return jsonify({
                "error": "Plata necesara pentru mai mult de %d bilete." % FREE_TICKETS,
                "need_payment": True,
                "price": compute_price(n_bilete),
            }), 402
        # marcam tokenul ca folosit (o singura generare per plata)
        rec["used"] = True
        _PAID_SESSIONS[sid] = rec
    pool = _clamp(int(request.args.get("pool", 20)), 6, 49)
    extra9 = _clamp(int(request.args.get("extra9", 3)), 1, 10)
    pool9 = _clamp(int(request.args.get("pool9", 22)), 6, 49)
    seed = int(request.args.get("seed", SEED))
    use_filter = request.args.get("filter", "1") != "0"
    force = request.args.get("force", "0") == "1"
    # ponderi scor (se normalizeaza ca sa insumeze 1)
    w_freq = max(0.0, float(request.args.get("wfreq", 0.4)))
    w_delay = max(0.0, float(request.args.get("wdelay", 0.3)))
    w_pair = max(0.0, float(request.args.get("wpair", 0.3)))
    wsum = w_freq + w_delay + w_pair
    if wsum <= 0:
        w_freq, w_delay, w_pair, wsum = 0.4, 0.3, 0.3, 1.0
    w_freq, w_delay, w_pair = w_freq / wsum, w_delay / wsum, w_pair / wsum

    # extra9 nu poate depasi pool9 - 6 (raman macar 6 fixe)
    if extra9 > pool9 - 6:
        extra9 = max(1, pool9 - 6)

    try:
        draws, meta, from_cache = get_draws(force=force)
    except Exception as e:
        return jsonify({"error": str(e)}), 422

    an = Analyzer(draws)
    an.compute_frequency()
    an.compute_delay()
    an.compute_pairs()
    an.compute_transition()
    an.compute_profile_stats()
    an.compute_score(w_freq=w_freq, w_delay=w_delay, w_pair=w_pair)

    # SEED reglabil -> reproductibil pentru acelasi set de date + aceiasi parametri
    rng = np.random.RandomState(seed)
    target, tickets6 = an.gen_tickets(rng, count=n_bilete, pool=pool, use_filter=use_filter)
    tickets9 = [an.extend_to_9(rng, t, pool=pool9, extra=extra9) for t in tickets6]

    top_numbers = sorted(an.score.items(), key=lambda x: -x[1])[:12]
    top_numbers = [{"n": n, "score": round(s, 4), "freq": an.frequency[n], "delay": an.delay[n]}
                   for n, s in top_numbers]
    top_pairs = [{"a": a, "b": b, "count": c} for (a, b), c in an.pair_counts.most_common(8)]

    def with_profile(tk):
        p = draw_profile(tk)
        return {"nums": tk, "sum": p["sum"], "ip": f'{p["odd"]}/{p["even"]}', "shape": p["shape"]}

    # matrice de tranzitie (Markov) normalizata
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
            "w_freq": round(w_freq, 3), "w_delay": round(w_delay, 3), "w_pair": round(w_pair, 3),
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
        "tickets6": [with_profile(t) for t in tickets6],
        "tickets9": tickets9,
    })


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/schemes.js")
def schemes_js():
    return send_from_directory(".", "schemes.js", mimetype="application/javascript")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
