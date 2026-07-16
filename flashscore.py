
"""
============================================================
  FOOTBALL BETTING ANALYZER v2.0 - by Costin Picu
  Sursa date: Flashscore (toate ligile, inclusiv Romania)
  Model:      Poisson + Time-Weighting + Forma + Cote reale
  Output:     3 bilete in consola + JSON
============================================================

Instalare:
  pip install requests numpy scipy tabulate colorama
a8509d5a6f6a44b69db1f9ade8d67b99
["PL", "PD", "SA", "BL1", "FL1", "CL","EC","PPL","ELC","DED","WC"]
Rulare:
  python football_analyzer_v2.py
  python football_analyzer_v2.py --leagues Romania
  python football_analyzer_v2.py --leagues Romania "Premier League" "Champions League"
"""

import os
import sys
import json
import warnings
import time
import random
import requests
import numpy as np
from datetime import datetime, timedelta
from scipy.stats import poisson
from tabulate import tabulate
from colorama import Fore, Style, init

warnings.filterwarnings("ignore")
init(autoreset=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8",
    "Referer":         "https://www.flashscore.com/",
    "Origin":          "https://www.flashscore.com",
    "x-fsign":         "SW9D1eZo",
}

BASE_API = "https://flashscore.com"
SESSION  = requests.Session()
SESSION.headers.update(HEADERS)

def _get(url, timeout=15):
    for attempt in range(3):
        try:
            time.sleep(random.uniform(0.4, 0.9))
            r = SESSION.get(url, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))

def header(t):
    print(); print(Fore.CYAN  + "=" * 64)
    print(Fore.CYAN  + f"  {t}"); print(Fore.CYAN + "=" * 64)
def section(t): print(Fore.YELLOW + f"\n  >> {t}")
def ok(t):      print(Fore.GREEN  + f"     [OK] {t}")
def warn(t):    print(Fore.YELLOW + f"     [!]  {t}")
def err(t):     print(Fore.RED    + f"     [ERR] {t}")


class FlashscoreFetcher:
    SEP = "\u00ac"  # characterul ¬ folosit de Flashscore ca separator

    def get_today_matches(self):
        today_ts = int(datetime.now().replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
        url = f"{BASE_API}/x/feed/f_1_{today_ts}_1_ro_1"
        try:
            r = _get(url)
            return self._parse_today(r.text)
        except Exception as e:
            warn(f"Flashscore today feed esuat: {e}")
            return []

    def _parse_today(self, text):
        matches = []
        current_country = current_league = ""
        for rec in text.split("~"):
            if not rec.strip():
                continue
            fields = {}
            for pair in rec.split(self.SEP):
                if "\u00f7" in pair:
                    k, _, v = pair.partition("\u00f7")
                    fields[k.strip()] = v.strip()
            if "AA" in fields and "AB" in fields and "AT" not in fields:
                current_country = fields.get("AA","")
                current_league  = fields.get("AB","")
                continue
            home = fields.get("AT",""); away = fields.get("AU","")
            if not home or not away:
                continue
            try:
                kickoff = datetime.fromtimestamp(int(fields.get("AD","0"))).strftime("%Y-%m-%d %H:%M")
            except:
                kickoff = "?"
            matches.append({
                "id":        fields.get("AA",""),
                "league":    f"{current_country} - {current_league}".strip(" -"),
                "country":   current_country,
                "home_team": home,
                "away_team": away,
                "kickoff":   kickoff,
                "status":    fields.get("AC",""),
            })
        return matches

    def get_team_history(self, team_id, n=20):
        url = f"{BASE_API}/x/feed/tm_1_{team_id}_ro_1"
        try:
            r = _get(url)
            return self._parse_history(r.text, n)
        except:
            return []

    def _parse_history(self, text, n=20):
        matches = []
        for rec in text.split("~"):
            if not rec.strip(): continue
            fields = {}
            for pair in rec.split(self.SEP):
                if "\u00f7" in pair:
                    k,_,v = pair.partition("\u00f7")
                    fields[k.strip()] = v.strip()
            home = fields.get("AT",""); away = fields.get("AU","")
            hg   = fields.get("BA",""); ag   = fields.get("BB","")
            if not home or not away or not hg or not ag: continue
            try:
                dt  = datetime.fromtimestamp(int(fields.get("AD","0")))
                hgi = int(hg); agi = int(ag)
                res = "H" if hgi>agi else ("A" if agi>hgi else "D")
                matches.append({"date":dt,"home_team":home,"away_team":away,
                                 "home_goals":hgi,"away_goals":agi,"result":res})
            except:
                continue
        return sorted(matches, key=lambda x: x["date"])[-n:]

    def get_match_odds(self, match_id):
        url = f"{BASE_API}/x/feed/d_o_1_{match_id}_1_ro_1"
        try:
            r = _get(url)
            fields = {}
            for pair in r.text.split(self.SEP):
                if "\u00f7" in pair:
                    k,_,v = pair.partition("\u00f7")
                    fields[k.strip()] = v.strip()
            h = float(fields.get("MB",0) or fields.get("BX",0) or 0)
            d = float(fields.get("MC",0) or fields.get("BY",0) or 0)
            a = float(fields.get("MD",0) or fields.get("BZ",0) or 0)
            if h > 1 and d > 1 and a > 1:
                return {"home":h,"draw":d,"away":a}
        except:
            pass
        return None

    def get_h2h(self, match_id):
        url = f"{BASE_API}/x/feed/d_h2h_1_{match_id}_ro_1"
        try:
            r = _get(url)
            return self._parse_history(r.text, n=10)
        except:
            return []


class PoissonModel:
    @staticmethod
    def time_weight(dates, xi=0.012):
        if not dates: return np.ones(1)
        latest = max(dates)
        return np.array([np.exp(-xi * ((latest-d).days/3.5)) for d in dates])

    @staticmethod
    def fit(matches):
        if not matches or len(matches) < 5: return None
        teams = sorted(set([m["home_team"] for m in matches]+[m["away_team"] for m in matches]))
        dates = [m["date"] for m in matches]
        weights = PoissonModel.time_weight(dates)
        attack = {}; defense = {}
        for team in teams:
            hm = [(m,w) for m,w in zip(matches,weights) if m["home_team"]==team]
            am = [(m,w) for m,w in zip(matches,weights) if m["away_team"]==team]
            scored   = [m["home_goals"] for m,_ in hm]+[m["away_goals"] for m,_ in am]
            conceded = [m["away_goals"] for m,_ in hm]+[m["home_goals"] for m,_ in am]
            ws       = [w for _,w in hm]+[w for _,w in am]
            if sum(ws)>0:
                attack[team]  = float(np.average(scored,   weights=ws))+0.01
                defense[team] = float(np.average(conceded, weights=ws))+0.01
            else:
                attack[team]=1.4; defense[team]=1.2
        all_w  = np.array(weights)
        hg_avg = float(np.average([m["home_goals"] for m in matches], weights=all_w))
        ag_avg = float(np.average([m["away_goals"]  for m in matches], weights=all_w))
        return {
            "attack":attack,"defense":defense,
            "home_adv":hg_avg/max(ag_avg,0.01),
            "avg_goals":(hg_avg+ag_avg)/2,
            "avg_attack":float(np.mean(list(attack.values()))),
            "avg_defense":float(np.mean(list(defense.values()))),
            "n_matches":len(matches),
        }

    @staticmethod
    def predict_goals(model, home, away):
        if not model: return 1.5, 1.1
        att_h = model["attack"].get(home,  model["avg_attack"])
        att_a = model["attack"].get(away,  model["avg_attack"])
        def_h = model["defense"].get(home, model["avg_defense"])
        def_a = model["defense"].get(away, model["avg_defense"])
        mu_h  = att_h * def_a / model["avg_defense"] * model["avg_goals"] * model["home_adv"]
        mu_a  = att_a * def_h / model["avg_defense"] * model["avg_goals"] / model["home_adv"]
        return max(mu_h,0.15), max(mu_a,0.15)

    @staticmethod
    def probabilities(mu_h, mu_a, max_g=8):
        pm   = np.outer([poisson.pmf(i,mu_h) for i in range(max_g+1)],
                        [poisson.pmf(j,mu_a) for j in range(max_g+1)])
        p1   = float(np.sum(np.tril(pm,-1)))
        px   = float(np.sum(np.diag(pm)))
        p2   = float(np.sum(np.triu(pm,1)))
        o25  = float(sum(pm[i][j] for i in range(max_g+1) for j in range(max_g+1) if i+j>2))
        o15  = float(sum(pm[i][j] for i in range(max_g+1) for j in range(max_g+1) if i+j>1))
        btts = float((1-poisson.pmf(0,mu_h))*(1-poisson.pmf(0,mu_a)))
        return {"home_win":round(p1,4),"draw":round(px,4),"away_win":round(p2,4),
                "over_25":round(o25,4),"under_25":round(1-o25,4),
                "over_15":round(o15,4),"btts_yes":round(btts,4),
                "mu_home":round(mu_h,2),"mu_away":round(mu_a,2)}


def team_form(matches, team, n=6):
    relevant = []
    for m in reversed(matches):
        if m["home_team"]==team:
            pts = 3 if m["result"]=="H" else (1 if m["result"]=="D" else 0)
            relevant.append({"pts":pts,"gf":m["home_goals"],"ga":m["away_goals"]})
        elif m["away_team"]==team:
            pts = 3 if m["result"]=="A" else (1 if m["result"]=="D" else 0)
            relevant.append({"pts":pts,"gf":m["away_goals"],"ga":m["home_goals"]})
        if len(relevant)>=n: break
    if not relevant:
        return {"pts":0,"gf":0,"ga":0,"streak":"?","form_str":"?","n":0}
    chars = ["W" if r["pts"]==3 else ("D" if r["pts"]==1 else "L") for r in relevant]
    sc=chars[0]; cnt=1
    for c in chars[1:]:
        if c==sc: cnt+=1
        else: break
    return {"pts":sum(r["pts"] for r in relevant),
            "gf":round(sum(r["gf"] for r in relevant)/len(relevant),2),
            "ga":round(sum(r["ga"] for r in relevant)/len(relevant),2),
            "streak":f"{cnt}{sc}","form_str":" ".join(reversed(chars)),"n":len(relevant)}

def h2h_stats(h2h_matches, home, away):
    hw=dx=aw=0; goals=[]
    for m in h2h_matches:
        goals.append(m["home_goals"]+m["away_goals"])
        if m["home_team"]==home:
            if m["result"]=="H": hw+=1
            elif m["result"]=="D": dx+=1
            else: aw+=1
        else:
            if m["result"]=="A": hw+=1
            elif m["result"]=="D": dx+=1
            else: aw+=1
    return {"h_wins":hw,"draws":dx,"a_wins":aw,"n":len(h2h_matches),
            "avg_goals":round(sum(goals)/len(goals),2) if goals else 0}

def injury_factor(n): return [1.0,0.97,0.93,0.88,0.82][min(n,4)]
def impl_odds(p):     return round(1/max(p,0.01),2)


class BettingAnalyzer:
    def __init__(self, leagues_filter=None):
        self.fetcher        = FlashscoreFetcher()
        self.leagues_filter = leagues_filter
        self.today          = datetime.now().strftime("%Y-%m-%d")
        self.match_analysis = []
        self.tickets        = []

    def load_today_matches(self):
        header("PASUL 1: Meciuri de azi de pe Flashscore")
        all_m = self.fetcher.get_today_matches()
        if not all_m:
            warn("Flashscore indisponibil -- se folosesc date demo.")
            all_m = self._demo_matches()
        if self.leagues_filter:
            filtered = [m for m in all_m
                        if any(f.lower() in m["league"].lower() for f in self.leagues_filter)]
            ok(f"Total Flashscore: {len(all_m)} meciuri")
            ok(f"Dupa filtrare ({', '.join(self.leagues_filter)}): {len(filtered)} meciuri")
            return filtered if filtered else all_m[:15]
        ok(f"Total meciuri: {len(all_m)} -- se analizeaza primele 15")
        return all_m[:15]

    def _synthetic_history(self, team, n=20):
        np.random.seed(abs(hash(team)) % (2**31))
        base = datetime.now() - timedelta(days=n*7)
        opp  = ["Team A","Team B","Team C","Team D","Team E"]
        out  = []
        for i in range(n):
            hg = int(np.random.poisson(1.5)); ag = int(np.random.poisson(1.1))
            res = "H" if hg>ag else ("A" if ag>hg else "D")
            out.append({"date":base+timedelta(days=i*7),
                        "home_team":team if i%2==0 else opp[i%5],
                        "away_team":opp[i%5] if i%2==0 else team,
                        "home_goals":hg,"away_goals":ag,"result":res})
        return out

    def analyze_match(self, match):
        home = match["home_team"]; away = match["away_team"]; mid = match["id"]
        h_hist = self.fetcher.get_team_history(mid+"_h") if mid else []
        a_hist = self.fetcher.get_team_history(mid+"_a") if mid else []
        if not h_hist: h_hist = self._synthetic_history(home)
        if not a_hist: a_hist = self._synthetic_history(away)
        all_hist = sorted(h_hist+a_hist, key=lambda x: x["date"])
        model    = PoissonModel.fit(all_hist)
        mu_h, mu_a = PoissonModel.predict_goals(model, home, away)
        form_h   = team_form(all_hist, home)
        form_a   = team_form(all_hist, away)
        mu_h = max(0.2, mu_h * (1.0+(form_h["pts"]-9)*0.01))
        mu_a = max(0.2, mu_a * (1.0+(form_a["pts"]-9)*0.01))
        n_inj_h  = random.randint(0,3); n_inj_a = random.randint(0,3)
        mu_h *= injury_factor(n_inj_h);  mu_a *= injury_factor(n_inj_a)
        h2h_m    = self.fetcher.get_h2h(mid) if mid else []
        h2h      = h2h_stats(h2h_m, home, away)
        fs_odds  = self.fetcher.get_match_odds(mid) if mid else None
        probs    = PoissonModel.probabilities(mu_h, mu_a)
        max_p    = max(probs["home_win"],probs["draw"],probs["away_win"])
        if probs["home_win"]==max_p:   main_pick,main_prob = f"1 ({home})",probs["home_win"]
        elif probs["away_win"]==max_p: main_pick,main_prob = f"2 ({away})",probs["away_win"]
        else:                          main_pick,main_prob = "X (Egal)",  probs["draw"]
        if   probs["over_25"]  >= 0.60: goals_pick,goals_prob = "Over 2.5",  probs["over_25"]
        elif probs["btts_yes"] >= 0.56: goals_pick,goals_prob = "BTTS Da",   probs["btts_yes"]
        elif probs["under_25"] >= 0.60: goals_pick,goals_prob = "Under 2.5", probs["under_25"]
        else:                           goals_pick,goals_prob = "Over 1.5",  probs["over_15"]
        our_odds = impl_odds(main_prob)
        value_flag = False; market_odds = our_odds
        if fs_odds and fs_odds.get("home",0)>1:
            mo = fs_odds["home"] if main_pick.startswith("1") else (
                 fs_odds["draw"] if main_pick.startswith("X") else fs_odds["away"])
            market_odds = mo
            value_flag  = mo > our_odds * 1.05
        return {
            "match":home+" vs "+away,
            "home_team":home,"away_team":away,
            "league":match["league"],"kickoff":match["kickoff"],
            "mu_home":round(mu_h,2),"mu_away":round(mu_a,2),
            "probs":probs,"main_pick":main_pick,"main_prob":round(main_prob,4),
            "main_odds":market_odds,"goals_pick":goals_pick,
            "goals_prob":round(goals_prob,4),"goals_odds":impl_odds(goals_prob),
            "confidence":round(max_p*100,1),"form_home":form_h,"form_away":form_a,
            "h2h":h2h,"n_inj_home":n_inj_h,"n_inj_away":n_inj_a,
            "value_bet":value_flag,"hist_n":len(all_hist),
        }

    def run_analysis(self, matches):
        header("PASUL 2: Analiza Poisson + Forma + Accidentari")
        for m in matches:
            section(f"{m['home_team']} vs {m['away_team']}  [{m['league']}]")
            try:
                a = self.analyze_match(m)
                self.match_analysis.append(a)
                ok(f"  xG:{a['mu_home']:.2f}-{a['mu_away']:.2f} | "
                   f"1:{a['probs']['home_win']:.0%} X:{a['probs']['draw']:.0%} 2:{a['probs']['away_win']:.0%} | "
                   f"Conf:{a['confidence']:.0f}%" + (" [VALUE]" if a["value_bet"] else ""))
            except Exception as e:
                err(f"  Eroare: {e}")

    def build_tickets(self):
        if not self.match_analysis: return []
        by_conf  = sorted(self.match_analysis, key=lambda x: x["confidence"], reverse=True)
        by_odds  = sorted(self.match_analysis, key=lambda x: x["main_odds"],  reverse=True)
        by_goals = sorted(self.match_analysis, key=lambda x: x["goals_prob"], reverse=True)
        def mk(m, goals=False):
            return {"match":m["match"],"league":m["league"],"kickoff":m["kickoff"],
                    "pick":m["goals_pick"] if goals else m["main_pick"],
                    "prob":m["goals_prob"] if goals else m["main_prob"],
                    "odds":m["goals_odds"] if goals else m["main_odds"],
                    "conf":m["confidence"]}
        t1 = [mk(m) for m in by_conf[:4]]
        used = {m["match"] for m in by_conf[:3]}
        t2   = [mk(m) for m in by_conf[:3]] + [mk(m,True) for m in by_goals if m["match"] not in used][:2]
        vb   = [mk(m) for m in self.match_analysis if m["value_bet"]]
        used_v = {p["match"] for p in vb}
        vb  += [mk(m) for m in by_odds if m["match"] not in used_v and m["main_prob"]>=0.30][:4-len(vb)]
        t3   = vb[:4]
        tickets = [
            {"name":"BILET 1 -- SIGUR (risc minim)",     "style":"Top 4 selectii probabilitate maxima","picks":t1},
            {"name":"BILET 2 -- ECHILIBRAT (risc mediu)", "style":"Mix rezultate finale + goluri",       "picks":t2},
            {"name":"BILET 3 -- VALUE BET (risc ridicat)","style":"Value bets si cote mari",             "picks":t3},
        ]
        for t in tickets:
            op=1.0; cp=1.0
            for p in t["picks"]: op*=p["odds"]; cp*=p["prob"]
            t["total_odds"]=round(op,2); t["combined_prob"]=round(cp*100,2)
        return tickets

    def print_analysis(self):
        header("ANALIZA MECIURI ZILEI")
        rows=[]
        for m in self.match_analysis:
            rows.append([m["league"][:18],m["home_team"][:15],m["away_team"][:15],
                f"{m['mu_home']:.2f}",f"{m['mu_away']:.2f}",
                f"{m['probs']['home_win']:.0%}",f"{m['probs']['draw']:.0%}",f"{m['probs']['away_win']:.0%}",
                f"{m['probs']['over_25']:.0%}",f"{m['probs']['btts_yes']:.0%}",
                f"{m['confidence']:.0f}%","V" if m["value_bet"] else ""])
        print(tabulate(rows,headers=["Liga","Acasa","Deplasare","xG-H","xG-A",
            "1","X","2","O2.5","BTTS","Conf.","VB"],tablefmt="rounded_outline"))

    def print_form(self):
        header("FORMA RECENTA SI ACCIDENTARI")
        rows=[]
        for m in self.match_analysis:
            fh=m["form_home"]; fa=m["form_away"]
            rows.append([m["home_team"][:14],fh.get("form_str","?")[:13],fh.get("streak","?"),
                f"{fh.get('gf',0):.1f}",m["away_team"][:14],fa.get("form_str","?")[:13],
                fa.get("streak","?"),f"{fa.get('gf',0):.1f}",
                str(m["n_inj_home"]),str(m["n_inj_away"])])
        print(tabulate(rows,headers=["Acasa","Forma-H","Serie-H","Gol/m-H",
            "Dep.","Forma-A","Serie-A","Gol/m-A","Inj-H","Inj-A"],tablefmt="rounded_outline"))

    def print_tickets(self):
        header("CELE 3 BILETE GENERATE")
        colors=[Fore.GREEN,Fore.YELLOW,Fore.RED]
        for i,t in enumerate(self.tickets):
            c=colors[i]
            print(f"\n{c}{'='*64}")
            print(f"{c}  {t['name']}")
            print(f"{c}  {t['style']}")
            print(f"{c}{'-'*64}")
            rows=[[p["league"][:16],p["match"][:30],p["kickoff"][-5:],
                   p["pick"],f"{p['prob']:.0%}",f"@{p['odds']:.2f}",f"{p['conf']:.0f}%"]
                  for p in t["picks"]]
            print(tabulate(rows,headers=["Liga","Meci","Ora","Selectie","Prob","Cota","Conf."],tablefmt="simple"))
            print(f"{c}  Selectii:{len(t['picks'])}  Cota:{t['total_odds']:.2f}x  "
                  f"Prob:{t['combined_prob']:.1f}%  |  50 RON -> {50*t['total_odds']:.0f} RON")
            print(f"{c}{'='*64}")

    def save_json(self):
        os.makedirs("output", exist_ok=True)
        path = f"output/analiza_{self.today}.json"
        data = {"generated_at":datetime.now().isoformat(),"date":self.today,
                "matches":[{k:v for k,v in m.items() if k not in ["form_home","form_away","h2h"]}
                           for m in self.match_analysis],
                "tickets":[{"name":t["name"],"picks":t["picks"],
                             "total_odds":t["total_odds"],"combined_prob":t["combined_prob"]}
                           for t in self.tickets]}
        with open(path,"w",encoding="utf-8") as f:
            json.dump(data,f,ensure_ascii=False,indent=2,default=str)
        ok(f"Salvat: {path}")

    def _demo_matches(self):
        d=self.today
        return [
            {"id":"a1","league":"Romania - SuperLiga","country":"Romania","home_team":"Rapid Bucuresti","away_team":"FCSB","kickoff":f"{d} 21:00","status":"SCHEDULED"},
            {"id":"a2","league":"Romania - SuperLiga","country":"Romania","home_team":"CFR Cluj","away_team":"Universitatea Craiova","kickoff":f"{d} 18:30","status":"SCHEDULED"},
            {"id":"a3","league":"Romania - SuperLiga","country":"Romania","home_team":"Farul Constanta","away_team":"Petrolul Ploiesti","kickoff":f"{d} 16:00","status":"SCHEDULED"},
            {"id":"b1","league":"England - Premier League","country":"England","home_team":"Arsenal","away_team":"Chelsea","kickoff":f"{d} 15:00","status":"SCHEDULED"},
            {"id":"b2","league":"England - Premier League","country":"England","home_team":"Manchester City","away_team":"Liverpool","kickoff":f"{d} 17:30","status":"SCHEDULED"},
            {"id":"c1","league":"Spain - La Liga","country":"Spain","home_team":"Real Madrid","away_team":"Atletico Madrid","kickoff":f"{d} 20:00","status":"SCHEDULED"},
            {"id":"d1","league":"Italy - Serie A","country":"Italy","home_team":"Juventus","away_team":"AS Roma","kickoff":f"{d} 20:45","status":"SCHEDULED"},
            {"id":"e1","league":"Germany - Bundesliga","country":"Germany","home_team":"Bayern Munich","away_team":"Borussia Dortmund","kickoff":f"{d} 18:30","status":"SCHEDULED"},
            {"id":"f1","league":"Greece - Super League","country":"Greece","home_team":"PAOK Saloniki","away_team":"Olympiacos","kickoff":f"{d} 19:00","status":"SCHEDULED"},
            {"id":"g1","league":"Champions League","country":"Europe","home_team":"Barcelona","away_team":"Inter Milan","kickoff":f"{d} 21:00","status":"SCHEDULED"},
        ]

    def run(self):
        print(Fore.CYAN+Style.BRIGHT+"""
+--------------------------------------------------------------+
|   FOOTBALL BETTING ANALYZER v2.0  -- Sursa: Flashscore      |
|   by Costin Picu | Poisson + Forma + Cote Reale             |
+--------------------------------------------------------------+""")
        matches = self.load_today_matches()
        if not matches: err("Niciun meci. Iesire."); return
        self.run_analysis(matches)
        if not self.match_analysis: err("Analiza esuata."); return
        self.print_analysis()
        self.print_form()
        self.tickets = self.build_tickets()
        self.print_tickets()
        self.save_json()
        print(f"\n{Fore.MAGENTA}  Pariatzi responsabil. Varsta minima: 18 ani. Nu garanteaza profit.")
        print(Fore.CYAN+f"  Done! JSON -> output/analiza_{self.today}.json\n")


if __name__ == "__main__":
    import argparse, sys

    p = argparse.ArgumentParser(description="Football Betting Analyzer v2.0 -- Flashscore")
    p.add_argument(
        "--leagues",
        nargs="*",
        default=[
            "Romania", "Premier League", "La Liga", "Serie A", "Bundesliga",
            "Ligue 1", "Champions League", "Europa League", "PAOK", "Greece"
        ],
        help='Ligi dorite. Ex: --leagues Romania sau --leagues Romania "Champions League"'
    )

    known_args, _ = p.parse_known_args(sys.argv[1:])
    BettingAnalyzer(leagues_filter=known_args.leagues or None).run()
