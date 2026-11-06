"""
Compilazione automatica della squadra: scelto l'allenatore, la rosa (4 G, 4 F, 2 C) con il punteggio atteso
piu' alto nella giornata (stima di scripts/stima_partita.py, tabella stima_giornata).

Programmazione lineare intera (scipy.optimize.milp, HiGHS): soluzione ottima esatta. Variabili binarie per
ogni giocatore i: x_i in rosa, s_i titolare, h_i sesto uomo, c_i capitano.
  punteggio atteso = somma_i  v_i * (0,5 x_i + 0,5 s_i + 0,5 h_i + 0,5 c_i)
    panchina 50%, titolari e sesto uomo 100%, capitano x1,5
  vincoli: 4 G, 4 F, 2 C; crediti <= budget - prezzo allenatore; max 3 per squadra NBA, allenatore compreso;
           5 titolari con 1-3 G, 1-3 F, 1-2 C (sono esattamente i moduli ammessi); 1 sesto uomo;
           capitano tra i titolari.
  v_i = probabilita' di giocare x media stimata (stima_giornata: la media e' "dato che gioca");
        0 se la sua squadra non gioca nella giornata.
Allenatore: punteggio non stimato (costante per la scelta), entra solo con prezzo e squadra NBA.

    python compila_squadra.py --coach Daigneault
"""

import argparse
import sqlite3
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

DB = Path(__file__).resolve().parent.parent / "data" / "dunkest.sqlite"
BUDGET = 95
ROSTER = {"Guard": 4, "Forward": 4, "Center": 2}
COURT = {"Guard": (1, 3), "Forward": (1, 3), "Center": (1, 2)}   # titolari per ruolo: moduli ammessi
MAX_SAME_TEAM = 3
BENCH, CAPTAIN = 0.5, 1.5
PLUS_A, PLUS_B, PLUS_C, PLUS_MIN, PLUS_MAX, PLUS_FLOOR = 0.025, 1.77, 0.5, -1.5, 1.5, 4.0   # docs/plusvalenze.md
MIN_DIFF = 2        # le squadre della classifica differiscono per almeno 2 giocatori (altrimenti cambia solo un panchinaro)
TIE = 1e-4          # peso del punteggio come spareggio quando si massimizza la plusvalenza


def candidates(db, matchday_id):
    """Giocatori del listone (fotografia piu' recente <= giornata) con il punteggio atteso della giornata."""
    snap = db.execute("SELECT COALESCE(MAX(matchday_id), (SELECT MAX(matchday_id) FROM fantasy_listone)) "
                      "FROM fantasy_listone WHERE matchday_id <= ?", (matchday_id,)).fetchone()[0]
    return db.execute("""SELECT l.fantasy_id, p.position, l.quotation, l.team_id,
                                COALESCE(s.fp_mean, 0) * COALESCE(s.probability_of_playing, 1),
                                COALESCE(s.fp_mean, 0), COALESCE(s.probability_of_playing, 1), s.fp_mean IS NOT NULL
                         FROM fantasy_listone l JOIN fantasy_players p USING (fantasy_id)
                         LEFT JOIN stima_giornata s ON s.fantasy_id = l.fantasy_id AND s.matchday_id = ?
                         WHERE l.matchday_id = ?""", (matchday_id, snap)).fetchall()


def plus_value(cr, pts, prob, plays):
    """Plusvalenza attesa (formula di docs/plusvalenze.md, come nella pagina): a punteggio medio, 4 crediti minimo;
    squadra a riposo 0; se non gioca pur avendo la squadra in campo -0,1 (peso: 1 - probabilita' di giocare)."""
    if not plays:
        return 0.0
    raw = np.floor((PLUS_A * (pts - PLUS_B * cr - PLUS_C)) * 10 + 0.5) / 10
    p = max(PLUS_FLOOR - cr, min(PLUS_MAX, max(PLUS_MIN, raw)))
    return float(prob * p + (1 - prob) * -0.1)


def prune(pl, objective, keep, drop):
    """Toglie i giocatori che non possono servire. B e' inutile se in almeno 6 squadre NBA diverse ci sono 3 o piu'
    giocatori dello stesso ruolo che costano non piu' di B e valgono non meno (punteggio e, per l'obiettivo
    plusvalenza, anche plusvalenza): in qualsiasi squadra con B si puo' mettere uno di questi, non gia' scelto e di una
    squadra NBA con posto libero (al massimo 4 squadre sono piene), senza perdere valore ne' superare i crediti.
    Il problema diventa molto piu' piccolo e la soluzione non cambia."""
    pl = [p for p in pl if p[0] not in drop]
    plus = {p[0]: plus_value(p[2], p[5], p[6], p[7]) for p in pl}
    out = []
    for p in pl:
        per_team = {}
        for q in pl:
            if (q is not p and q[1] == p[1] and q[2] <= p[2] and q[4] >= p[4]
                    and (objective == "punteggio" or plus[q[0]] >= plus[p[0]])
                    and not (q[2] == p[2] and q[4] == p[4] and plus[q[0]] == plus[p[0]] and q[0] > p[0])):
                per_team[q[3]] = per_team.get(q[3], 0) + 1
        useless = sum(1 for n in per_team.values() if n >= 3) >= 6
        if p[0] in keep or not useless:
            out.append(p)
    return out


def solve(db, coach_id, matchday_id, budget=BUDGET, include=(), exclude=(), objective="punteggio", top=1,
          min_diff=MIN_DIFF):
    """Le prime `top` squadre per obiettivo ('punteggio' atteso o 'plusvalenza' attesa in crediti), diverse
    tra loro per almeno `min_diff` giocatori. Con 'plusvalenza' il punteggio atteso fa da spareggio."""
    if objective not in ("punteggio", "plusvalenza"):
        raise ValueError("Obiettivo non valido")
    if not db.execute("SELECT 1 FROM stima_giornata WHERE matchday_id = ? LIMIT 1", (matchday_id,)).fetchone():
        raise ValueError("Stime non calcolate per questa giornata: lancia aggiorna_stime.bat")
    players = candidates(db, matchday_id)
    coach = next((p for p in players if p[0] == coach_id and p[1] == "Head Coach"), None)
    if coach is None:
        raise ValueError("Allenatore non trovato nel listone")
    pl = prune([p for p in players if p[1] in ROSTER], objective, keep=set(include), drop=set(exclude))
    n = len(pl)
    role = np.array([p[1] for p in pl])
    cr = np.array([p[2] for p in pl], dtype=float)
    team = np.array([p[3] for p in pl])
    v = np.array([p[4] for p in pl], dtype=float)
    plus = np.array([plus_value(p[2], p[5], p[6], p[7]) for p in pl])

    # variabili [x | s | h | c], n ciascuna
    I, Z = np.eye(n), np.zeros((n, n))
    score = np.concatenate([BENCH * v, (1 - BENCH) * v, (1 - BENCH) * v, (CAPTAIN - 1) * v])
    zero = np.zeros(n)
    obj = score if objective == "punteggio" else np.concatenate([plus, zero, zero, zero]) + TIE * score
    cons = []
    for r, k in ROSTER.items():
        m = (role == r).astype(float)
        cons.append(LinearConstraint(np.concatenate([m, zero, zero, zero]), k, k))
        a, b = COURT[r]
        cons.append(LinearConstraint(np.concatenate([zero, m, zero, zero]), a, b))
    cons.append(LinearConstraint(np.concatenate([cr, zero, zero, zero]), -np.inf, budget - coach[2]))
    for t in set(team):
        m = (team == t).astype(float)
        cons.append(LinearConstraint(np.concatenate([m, zero, zero, zero]), 0, MAX_SAME_TEAM - (coach[3] == t)))
    one = np.ones(n)
    cons.append(LinearConstraint(np.concatenate([zero, one, zero, zero]), 5, 5))
    cons.append(LinearConstraint(np.concatenate([zero, zero, one, zero]), 1, 1))
    cons.append(LinearConstraint(np.concatenate([zero, zero, zero, one]), 1, 1))
    cons.append(LinearConstraint(np.hstack([-I, I, I, Z]), -np.inf, 0))     # titolare o sesto -> in rosa
    cons.append(LinearConstraint(np.hstack([Z, -I, Z, I]), -np.inf, 0))     # capitano -> titolare
    ids = [p[0] for p in pl]
    ub = np.ones(4 * n)
    lb = np.zeros(4 * n)
    if set(include) & set(exclude):
        raise ValueError("Un giocatore non può essere sia da avere sia da escludere")
    for fid in exclude:                       # fuori dalla rosa (e quindi da titolari, sesto uomo, capitano)
        if fid in ids:
            ub[ids.index(fid)] = 0
    for fid in include:                       # obbligati in rosa
        if fid in ids:
            lb[ids.index(fid)] = 1

    teams = []
    for _ in range(top):
        res = milp(-obj, integrality=np.ones(4 * n), bounds=Bounds(lb, ub), constraints=cons,
                   options={"time_limit": 30, "mip_rel_gap": 0})
        if res.x is None:
            break
        x, s, h, c = (res.x.reshape(4, n) > 0.5)
        roster = np.flatnonzero(x)
        bench = sorted((i for i in roster if not s[i] and not h[i]), key=lambda i: -v[i])
        teams.append({"coach": coach_id, "matchday_id": matchday_id,
                      "starters": [pl[i][0] for i in roster if s[i]], "sixth": pl[int(np.flatnonzero(h)[0])][0],
                      "bench": [pl[i][0] for i in bench], "captain": pl[int(np.flatnonzero(c)[0])][0],
                      "credits": round(float(cr[roster].sum() + coach[2]), 1),
                      "expected": round(float(score @ res.x), 1), "plus": round(float(plus[roster].sum()), 1),
                      "optimal": bool(res.status == 0)})
        # la prossima squadra deve differire da questa per almeno min_diff giocatori
        cut = np.concatenate([x.astype(float), zero, zero, zero])
        cons = cons + [LinearConstraint(cut, -np.inf, 10 - min_diff)]
    if not teams:
        raise ValueError("Nessuna squadra possibile con questi vincoli (crediti, ruoli, massimo 3 per squadra NBA)")
    return teams


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coach", required=True, help="cognome dell'allenatore")
    ap.add_argument("--crediti", type=float, default=BUDGET)
    ap.add_argument("--obiettivo", choices=["punteggio", "plusvalenza"], default="punteggio")
    ap.add_argument("--top", type=int, default=1)
    args = ap.parse_args()
    db = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    md = db.execute("SELECT MAX(matchday_id) FROM fantasy_listone").fetchone()[0]
    cid = db.execute("SELECT fantasy_id FROM fantasy_players WHERE position = 'Head Coach' AND last_name LIKE ?",
                     (f"%{args.coach}%",)).fetchone()[0]
    outs = solve(db, cid, md, args.crediti, objective=args.obiettivo, top=args.top)
    name = {r[0]: f"{r[1]} {r[2]} ({r[3][0]}, {r[4]})" for r in db.execute(
        """SELECT p.fantasy_id, p.first_name, p.last_name, p.position, l.quotation FROM fantasy_players p
           JOIN fantasy_listone l USING (fantasy_id) WHERE l.matchday_id = ?""", (md,))}
    for k, out in enumerate(outs, 1):
        print(f"#{k} allenatore {name[cid]} | crediti {out['credits']} | punteggio {out['expected']} | "
              f"plusvalenza {out['plus']} | ottimo {out['optimal']}")
        print("  quintetto:", ", ".join(name[f] + (" [C]" if f == out["captain"] else "") for f in out["starters"]))
        print("  sesto:", name[out["sixth"]], "| panchina:", ", ".join(name[f] for f in out["bench"]))


if __name__ == "__main__":
    main()
