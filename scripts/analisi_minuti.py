"""
Analisi 1 sui minuti (descrittiva), stagioni 2018/19-2024/25 (il 2025/26 resta per il test).

  1. quanto prevedono i minuti le sole medie del giocatore, per fase della stagione;
  2. di quanto si spostano gli scarti (minuti reali - previsti dalle medie) in base a
     compagni assenti, differenza di forza, back-to-back, rientro da assenza, margine finale.

Minuti = quota dei minuti di squadra x 240 (neutralizza i supplementari).
Medie e intervalli al 95% con errori standard raggruppati per giocatore.

    python analisi_minuti.py      -> docs/analisi_minuti.md
"""

import sqlite3

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from stima_dati import DB, OUT as DATASET, ROOT

REPORT = ROOT / "docs" / "analisi_minuti.md"
SEASONS = [1, 3, 5, 7, 9, 13, 19]
MIN_HISTORY = 10


def load():
    d = pd.read_parquet(DATASET)
    db = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    tm = pd.read_sql("""SELECT game_id, team_id, SUM(minutes) AS team_min, SUM(pts) AS team_pts
                        FROM player_games GROUP BY 1, 2""", db)
    opp = tm.rename(columns={"team_id": "opponent_team_id", "team_pts": "opp_pts"})[
        ["game_id", "opponent_team_id", "opp_pts"]]
    d = d.merge(tm, on=["game_id", "team_id"]).merge(opp, on=["game_id", "opponent_team_id"])
    d["min240"] = d["minutes"] / d["team_min"] * 240
    d["final_margin"] = d["team_pts"] - d["opp_pts"]
    d["strength_diff"] = d["t_margin"] - d["o_margin"]

    # media minuti (su 240) della stagione precedente giocata
    order = {s: i for i, s in enumerate(sorted(d["season_id"].unique()))}
    ps = d.groupby(["player_id", "season_id"])["min240"].mean().reset_index()
    ps["season_idx"] = ps["season_id"].map(order) + 1
    d["season_idx"] = d["season_id"].map(order)
    d = d.merge(ps[["player_id", "season_idx", "min240"]].rename(columns={"min240": "prev_season_min"}),
                on=["player_id", "season_idx"], how="left")

    d = d[d["season_id"].isin(SEASONS) & (d["career_games"] >= MIN_HISTORY)].copy()
    d["phase"] = pd.cut(d["season_games"], [-1, 9, 29, 999], labels=["1-10", "11-30", "31+"])
    d["role"] = np.where(d["ms_starter"] >= 0.5, "titolare", "riserva")
    return d


def fmt(df):
    return df.to_markdown(floatfmt=".2f")


def baseline(d, out):
    d = d.copy()
    d["prev_filled"] = d["prev_season_min"].fillna(d["ml_min"])
    fit = smf.ols("min240 ~ ms_min + ml_min + prev_filled + last_min", data=d).fit()
    d["comb"] = fit.fittedvalues
    preds = {"media recente (5)": "ms_min", "media lunga (25)": "ml_min",
             "stagione precedente": "prev_filled", "combinazione (OLS)": "comb"}
    rows = []
    for phase, g in d.groupby("phase", observed=True):
        for name, col in preds.items():
            e = g["min240"] - g[col]
            rows.append({"fase (partite)": phase, "previsione": name, "n": len(g),
                         "MAE": e.abs().mean(), "bias": -e.mean(),
                         "R2": 1 - (e ** 2).sum() / ((g["min240"] - g["min240"].mean()) ** 2).sum()})
    t = pd.DataFrame(rows).pivot(index="previsione", columns="fase (partite)", values=["MAE", "R2"])
    out.append("## 1. Previsione con le sole medie del giocatore\n")
    out.append("MAE in minuti (su 240), R2. Combinazione: " +
               ", ".join(f"{k} {v:+.2f}" for k, v in fit.params.items()) + "\n")
    out.append(fmt(t) + "\n")
    out.append("Errore della combinazione per fascia di minuti abituali (MAE):\n")
    d["fascia"] = pd.cut(d["ml_min"], [0, 15, 28, 48], labels=["<15", "15-28", ">28"])
    out.append(fmt(d.assign(e=(d["min240"] - d["comb"]).abs())
                   .pivot_table(index="fascia", columns="phase", values="e", aggfunc="mean", observed=True)) + "\n")
    d["resid"] = d["min240"] - d["comb"]
    return d


def by_bins(d, col, bins, labels, title, out, split=None):
    d = d.assign(b=pd.cut(d[col], bins, labels=labels))
    groups = [("tutti", d)] if split is None else list(d.groupby(split))
    rows = []
    for gname, g in groups:
        g = g.dropna(subset=["b"])
        m = smf.ols("resid ~ C(b) - 1", data=g).fit(cov_type="cluster",
                                                    cov_kwds={"groups": g["player_id"]})
        ci = m.conf_int()
        for lab in labels:
            k = f"C(b)[{lab}]"
            if k in m.params:
                rows.append({"gruppo": gname, "fascia": lab, "n": int((g["b"] == lab).sum()),
                             "scarto medio": m.params[k], "IC95": f"[{ci.loc[k, 0]:+.2f}, {ci.loc[k, 1]:+.2f}]"})
    out.append(f"### {title}\n")
    out.append(pd.DataFrame(rows).to_markdown(index=False, floatfmt="+.2f") + "\n")


def residuals(d, out):
    out.append("## 2. Scarti: minuti reali - combinazione delle medie\n")
    out.append("Scarto medio positivo = gioca piu' del solito. IC al 95% raggruppati per giocatore.\n")
    by_bins(d, "miss_min_same_pos", [-0.1, 0.1, 15, 30, 45, 300], ["0", "0-15", "15-30", "30-45", ">45"],
            "Minuti abituali dei compagni assenti nello STESSO ruolo (G/F/C)", out, split="position")
    d2 = d.assign(miss_other=d["miss_min"] - d["miss_min_same_pos"])
    by_bins(d2, "miss_other", [-0.1, 0.1, 20, 40, 60, 400], ["0", "0-20", "20-40", "40-60", ">60"],
            "Minuti abituali dei compagni assenti negli ALTRI ruoli", out)
    d3 = d.assign(fav=d["strength_diff"])
    by_bins(d3, "fav", [-30, -9, -6, -3, 3, 6, 9, 30], ["< -9", "-9/-6", "-6/-3", "-3/+3", "+3/+6", "+6/+9", "> +9"],
            "Differenza di forza attesa (margine medio squadra - avversario; + = favorita)", out, split="role")
    d4 = d.assign(absm=d["final_margin"].abs())
    by_bins(d4, "absm", [-1, 5, 10, 20, 30, 80], ["0-5", "5-10", "10-20", "20-30", ">30"],
            "Margine finale effettivo (solo descrittivo: garbage time)", out, split="role")
    by_bins(d, "t_rest", [0, 1, 2, 3, 400], ["1 (b2b)", "2", "3", "4+"],
            "Giorni di riposo della squadra", out, split="role")
    by_bins(d, "missed_before", [-0.1, 0.1, 2, 9, 200], ["0", "1-2", "3-9", "10+"],
            "Partite saltate di fila prima di questa (rientro)", out)

    out.append("## 3. Tutti insieme (regressione sullo scarto, effetti parziali)\n")
    d = d.assign(miss_other=d["miss_min"] - d["miss_min_same_pos"], b2b=(d["t_rest"] <= 1).astype(int),
                 rientro=(d["missed_before"] >= 3).astype(int), titolare=(d["role"] == "titolare").astype(int),
                 diff=d["strength_diff"].fillna(0).abs())
    m = smf.ols("resid ~ miss_min_same_pos:C(position) + miss_other + diff:titolare + diff:I(1-titolare)"
                " + b2b:titolare + rientro", data=d.dropna(subset=["resid"])
                ).fit(cov_type="cluster", cov_kwds={"groups": d.dropna(subset=['resid'])["player_id"]})
    t = pd.DataFrame({"coef": m.params, "IC basso": m.conf_int()[0], "IC alto": m.conf_int()[1]})
    out.append("Coefficienti: minuti in piu' per ogni minuto di assenti / punto di differenza / evento.\n")
    out.append(t.to_markdown(floatfmt="+.3f") + f"\n\nR2 sullo scarto: {m.rsquared:.3f}\n")


def main():
    d = load()
    out = ["# Analisi minuti (generato da scripts/analisi_minuti.py)\n",
           f"Stagioni 2018/19-2024/25, giocatori con almeno {MIN_HISTORY} partite di storico: {len(d):,} partite.\n"]
    d = baseline(d, out)
    residuals(d, out)
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
