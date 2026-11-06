"""
Forza delle squadre calcolata dalle ROSE (chi gioca), non dai risultati passati della squadra.

Per ogni squadra e partita: profilo atteso della squadra = somma sui giocatori disponibili di
(tasso al minuto del giocatore, media esponenziale prima della partita) x (minuti abituali),
riscalato a 240 minuti. Statistiche usate: punti, rimbalzi off/dif, assist, recuperi, stoppate,
palle perse, tiri, liberi, triple, +/- al minuto.
Una regressione lineare (stagioni 2018/19-2024/25) impara quanto ogni componente pesa sul margine:
  margine = rating(squadra) - rating(avversario) + vantaggio casa
Cosi' la forza cambia subito con mercato, rookie e infortuni.

Uscite:
  data/forza_squadre.parquet   rating prima di ogni partita storica (game_id, team_id, t_rating, o_rating)
  data/models/forza.json       pesi del modello (per le rose attuali, vedi current_ratings)
  docs/forza_squadre.md        confronto con la forza "dai risultati" sul 2025/26

    python forza_squadre.py
"""

import json
import sqlite3

import numpy as np
import pandas as pd
import statsmodels.api as sm

from stima_dati import DB, ROOT, load

OUT = ROOT / "data" / "forza_squadre.parquet"
WEIGHTS = ROOT / "data" / "models" / "forza.json"
REPORT = ROOT / "docs" / "forza_squadre.md"
BASE_DATASET = ROOT / "data" / "stima_dataset.parquet"
RATE_STATS = ["pts", "oreb", "dreb", "ast", "stl", "blk", "tov", "fga", "fta", "tpm", "pm"]
HALF_LIFE = 25
DEFAULT_MIN = 10.0              # minuti abituali di chi esordisce
TRAIN_MAX_SEASON = 19
TEST_SEASON = 25


def player_rates(pg):
    """Tassi al minuto e minuti abituali di ogni giocatore PRIMA di ogni partita."""
    pg = pg.sort_values(["player_id", "game_date", "game_id"]).copy()
    cols = RATE_STATS + ["minutes"]
    shifted = pg.groupby("player_id")[cols].shift()
    ew = (shifted.groupby(pg["player_id"]).ewm(halflife=HALF_LIFE, ignore_na=True).mean()
          .reset_index(level=0, drop=True).sort_index())
    for c in RATE_STATS:
        pg[f"r_{c}"] = ew[c] / ew["minutes"]
    pg["usual_min"] = ew["minutes"]
    return pg


def fill_rates(df, league):
    """Chi non ha storico: tassi medi del ruolo e minuti di default."""
    for c in RATE_STATS:
        df[f"r_{c}"] = df[f"r_{c}"].fillna(df["position"].map(league[c]))
    df["usual_min"] = df["usual_min"].fillna(DEFAULT_MIN)
    return df


def team_profile(df, minutes_col="usual_min"):
    """Profilo atteso per squadra (e partita): somma tasso x minuti, riscalata a 240."""
    keys = [k for k in ("game_id", "team_id") if k in df.columns]
    w = df[minutes_col] * 240 / df.groupby(keys)[minutes_col].transform("sum")
    prof = pd.DataFrame({f"x_{c}": w * df[f"r_{c}"] for c in RATE_STATS}, index=df.index)
    prof[keys] = df[keys]
    return prof.groupby(keys).sum().reset_index()


def main():
    db = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    pg = load(db)
    pm = pd.read_sql("SELECT game_id, player_id, plus_minus AS pm FROM player_games", db)
    pg = pg.merge(pm, on=["game_id", "player_id"])
    pg = player_rates(pg)
    train = pg["season_id"] <= TRAIN_MAX_SEASON
    tot = pg[train].groupby("position")[RATE_STATS + ["minutes"]].sum()
    league = {c: tot[c] / tot["minutes"] for c in RATE_STATS}
    pg = fill_rates(pg, league)

    prof = team_profile(pg)
    games = (pg.groupby(["game_id", "team_id"])
             .agg(season_id=("season_id", "first"), opp=("opponent_team_id", "first"), home=("home", "first"),
                  pts=("pts", "sum"), win=("win", "max")).reset_index())
    games = games.merge(prof, on=["game_id", "team_id"])
    o = games[["game_id", "team_id", "pts"] + [f"x_{c}" for c in RATE_STATS]].rename(
        columns={"team_id": "opp", "pts": "opp_pts"} | {f"x_{c}": f"o_{c}" for c in RATE_STATS})
    games = games.merge(o, on=["game_id", "opp"])
    games["margin"] = games["pts"] - games["opp_pts"]

    xcols = [f"x_{c}" for c in RATE_STATS]
    tr = games[games["season_id"] <= TRAIN_MAX_SEASON]
    mean = tr[xcols].mean()
    X = lambda g: sm.add_constant(pd.DataFrame(
        {c: g[f"x_{c}"] - g[f"o_{c}"] for c in RATE_STATS} | {"home": g["home"]}), has_constant="add")
    fit = sm.OLS(tr["margin"], X(tr)).fit()
    beta = fit.params[RATE_STATS]
    rating = lambda g, p: ((g[[f"{p}_{c}" for c in RATE_STATS]].values - mean.values) * beta.values).sum(axis=1)
    games["t_rating"] = rating(games, "x")
    games["o_rating"] = rating(games, "o")
    games["pred_margin"] = fit.predict(X(games))

    # confronto sul 2025/26 con la forza "dai risultati" (media esponenziale del margine della squadra)
    base = pd.read_parquet(BASE_DATASET, columns=["game_id", "team_id", "t_margin", "o_margin"]).drop_duplicates(
        ["game_id", "team_id"])
    te = games[games["season_id"] == TEST_SEASON].merge(base, on=["game_id", "team_id"], how="left")
    te["old_diff"] = te["t_margin"] - te["o_margin"]
    rows = []
    for name, x in [("risultati passati (attuale)", te[["old_diff", "home"]]),
                    ("rose (nuovo)", te[["pred_margin"]]),
                    ("rose + risultati", te[["pred_margin", "old_diff"]])]:
        xx = sm.add_constant(x.fillna(0), has_constant="add")
        lin = sm.OLS(te["margin"], xx).fit()
        lg = sm.Logit(te["win"], xx).fit(disp=0)
        p = lg.predict(xx)
        rows.append({"forza da": name, "R2 margine": lin.rsquared, "MAE margine": np.mean(np.abs(lin.resid)),
                     "log-loss vittoria": -np.mean(te["win"] * np.log(p) + (1 - te["win"]) * np.log(1 - p)),
                     "accuratezza vittoria": np.mean((p > 0.5) == te["win"])})
    res = pd.DataFrame(rows).set_index("forza da")

    WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS.write_text(json.dumps({"beta": beta.to_dict(), "mean": mean.to_dict(), "home": fit.params["home"],
                                   "league": {c: league[c].to_dict() for c in RATE_STATS}}, indent=1),
                       encoding="utf-8")
    games[["game_id", "team_id", "t_rating", "o_rating", "pred_margin"]].to_parquet(OUT, index=False)

    out = ["# Forza delle squadre dalle rose (generato da scripts/forza_squadre.py)", "",
           "Test sul 2025/26 (adattamento sul test stesso: confronta solo quanta informazione contiene ogni misura).", "",
           res.to_markdown(floatfmt=".3f"), "",
           "## Pesi (punti di margine per unita' di differenza nel profilo atteso)", "",
           pd.DataFrame({"peso": fit.params, "t": fit.tvalues}).to_markdown(floatfmt=".3f"), "",
           f"R2 in allenamento: {fit.rsquared:.3f}", ""]
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


def current_ratings(roster, min_col="pred_min"):
    """Rating delle squadre attuali. roster: team_id, position, minuti previsti (min_col) e tassi r_<stat>
    (da player_rates sull'ultima partita, senza shift), solo giocatori disponibili."""
    w = json.loads(WEIGHTS.read_text(encoding="utf-8"))
    league = {c: pd.Series(w["league"][c]) for c in RATE_STATS}
    r = roster.copy()
    r["usual_min"] = r[min_col]
    r = fill_rates(r, league)
    prof = team_profile(r[["team_id", "position", "usual_min"] + [f"r_{c}" for c in RATE_STATS]])
    x = prof[[f"x_{c}" for c in RATE_STATS]].values - np.array([w["mean"][f"x_{c}"] for c in RATE_STATS])
    prof["rating"] = (x * np.array([w["beta"][c] for c in RATE_STATS])).sum(axis=1)
    return prof[["team_id", "rating"]]


if __name__ == "__main__":
    main()
