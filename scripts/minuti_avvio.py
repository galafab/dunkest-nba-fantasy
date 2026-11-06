"""
Minuti di inizio stagione: per ogni giocatore della rosa, minuti medi (su 240) nelle prime
10 partite della squadra, stimati SOLO dalla stagione precedente + la rosa di inizio stagione.

Storico: la rosa di inizio stagione = chi ha giocato almeno una delle prime 10 partite della
squadra (gli infortunati all'avvio quindi non contano come concorrenti, come succedera' in
produzione con la lista infortuni). Obiettivo = minuti medi nelle partite giocate tra quelle 10.

Confronto con validazione "in avanti" (ogni avvio dal 2021/22 al 2025/26 previsto allenando solo
sugli avvii precedenti), errori anche per fascia di minuti:
  ingenuo   minuti medi della stagione precedente (rookie: media storica per fascia di draft)
  glm       regressione lineare con giocatore + concorrenza in rosa
  lgbm      LightGBM con le stesse variabili
  ... ognuno anche con la chiusura a 240 per squadra.

    python minuti_avvio.py             -> validazione, docs/minuti_avvio.md
    python minuti_avvio.py --prevedi   -> minuti previsti per le rose attuali (tabella stima_minuti_avvio)
"""

import sqlite3

import lightgbm as lgb
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from stima_dati import DB, ROOT, dunkest_base

REPORT = ROOT / "docs" / "minuti_avvio.md"
SEASONS = [1, 3, 5, 7, 9, 13, 19, 25]
NEW_SEASON = 99                          # stagione da prevedere (2026/27), id fittizio
START_YEAR = {1: 2018, 3: 2019, 5: 2020, 7: 2021, 9: 2022, 13: 2023, 19: 2024, 25: 2025, NEW_SEASON: 2026}
MIN_PLAY_PROB = 0.5                      # sotto questa probabilita' di giocare (listone) = assente all'avvio
TEST_SEASONS = [7, 9, 13, 19, 25]       # 2021/22 ... 2025/26
FIRST_N = 10
CAP = 40.0
# scala "taglia" del ruolo Basketball Reference (per la somiglianza tra ruoli)
SIZE = {"G": 1, "G-F": 2, "F-G": 2, "F": 3, "F-C": 4, "C-F": 4, "C": 5}
DRAFT_BINS = [0, 5, 14, 30, 60, 99]
DRAFT_LABELS = ["1-5", "6-14", "15-30", "31-60", "non scelto"]


# --------------------------------------------------------------------------- dati

def load_games(db):
    pg = pd.read_sql("""
        SELECT g.game_id, g.player_id, g.season_id, g.game_date, g.team_id, g.minutes, g.starter,
               g.pts, g.oreb, g.dreb, g.ast, g.stl, g.blk, g.blka, g.tov, g.fgm, g.fga, g.ftm, g.fta, g.tpm
        FROM player_games g WHERE g.minutes > 0""", db)
    pg["min240"] = pg["minutes"] / pg.groupby(["game_id", "team_id"])["minutes"].transform("sum") * 240
    pg["fp"] = dunkest_base(pg)
    return pg


def load_bio(db):
    return pd.read_sql("""
        SELECT pb.player_id, p.position AS dk_pos, b.pos AS bref_pos, b.birth_date, d.pick
        FROM players_bio pb JOIN players p USING (player_id)
        LEFT JOIN bref_players b USING (bref_id) LEFT JOIN bref_draft d USING (bref_id)
        WHERE pb.player_id IS NOT NULL""", db).drop_duplicates("player_id")


def season_summary(pg):
    """Riassunto di ogni giocatore in ogni stagione (tutte le squadre)."""
    pg = pg.sort_values(["game_date", "game_id"])
    g = pg.groupby(["player_id", "season_id"])
    s = g.agg(gp=("game_id", "size"), min=("min240", "mean"), start=("starter", "mean"),
              fp=("fp", "sum"), mins=("minutes", "sum"), pts=("pts", "sum"), fga=("fga", "sum"),
              fta=("fta", "sum"), tov=("tov", "sum"), last_team=("team_id", "last"))
    s["min_last20"] = g["min240"].apply(lambda x: x.tail(20).mean())
    s["fp36"] = s["fp"] / s["mins"] * 36
    s["usage36"] = (s["fga"] + 0.44 * s["fta"] + s["tov"]) / s["mins"] * 36
    s["ts"] = s["pts"] / (2 * (s["fga"] + 0.44 * s["fta"])).replace(0, np.nan)
    return s[["gp", "min", "min_last20", "start", "fp36", "usage36", "ts", "last_team"]].reset_index()


def start_rosters(pg):
    """Per squadra e stagione: chi gioca nelle prime 10 partite, minuti medi e partite giocate."""
    tg = (pg[["season_id", "team_id", "game_id", "game_date"]].drop_duplicates()
          .sort_values(["game_date", "game_id"]))
    tg["n"] = tg.groupby(["season_id", "team_id"]).cumcount()
    first = tg[tg["n"] < FIRST_N][["season_id", "team_id", "game_id"]]
    r = pg.merge(first, on=["season_id", "team_id", "game_id"])
    return (r.groupby(["season_id", "team_id", "player_id"])
            .agg(target=("min240", "mean"), n_played=("game_id", "size")).reset_index())


PREV_OF = {s: SEASONS[i - 1] for i, s in enumerate(SEASONS) if i > 0} | {NEW_SEASON: SEASONS[-1]}


def attach_history(rows, pg):
    """Aggiunge alle righe (player_id, season_id, team_id) la stagione precedente e quella prima."""
    summ = season_summary(pg)
    rows["prev_season"] = rows["season_id"].map(PREV_OF)
    rows["prev2_season"] = rows["prev_season"].map(PREV_OF)
    p1 = summ.rename(columns={c: f"prev_{c}" for c in summ.columns if c not in ("player_id", "season_id")})
    rows = rows.merge(p1.rename(columns={"season_id": "prev_season"}), on=["player_id", "prev_season"], how="left")
    p2 = summ[["player_id", "season_id", "min", "gp"]].rename(
        columns={"season_id": "prev2_season", "min": "prev2_min", "gp": "prev2_gp"})
    rows = rows.merge(p2, on=["player_id", "prev2_season"], how="left")
    # prima stagione giocata (chi non ha mai giocato e' rookie nella stagione della riga)
    first_season = pg.groupby("player_id")["season_id"].min().rename("first_season")
    rows = rows.join(first_season, on="player_id")
    rows["first_season"] = rows["first_season"].fillna(rows["season_id"])
    return rows


def build(pg, bio):
    rows = start_rosters(pg)
    rows = rows[rows["season_id"].isin(PREV_OF)].copy()
    return add_player_features(attach_history(rows, pg), bio)


def current_rows(db, pg):
    """Rose attuali dal listone fantasy (stagione nuova). Giocatori senza statistiche:
    player_id = -fantasy_id, cosi' si possono abbinare anagrafica e draft."""
    fp = pd.read_sql("""
        SELECT f.fantasy_id, f.first_name || ' ' || f.last_name AS name, f.team_id, f.position,
               f.stats_player_id, l.probability_of_playing, l.is_injured
        FROM fantasy_players f
        LEFT JOIN fantasy_listone l ON l.fantasy_id = f.fantasy_id
             AND l.matchday_id = (SELECT MAX(matchday_id) FROM fantasy_listone)
        WHERE f.position != 'Head Coach'""", db)
    fp["player_id"] = fp["stats_player_id"].fillna(-fp["fantasy_id"]).astype(int)
    bio = pd.read_sql("""
        SELECT COALESCE(pb.player_id, -pb.fantasy_id) AS player_id, b.pos AS bref_pos, b.birth_date, d.pick
        FROM players_bio pb LEFT JOIN bref_players b USING (bref_id) LEFT JOIN bref_draft d USING (bref_id)
        """, db).drop_duplicates("player_id")
    bio = bio.merge(fp[["player_id", "position"]], on="player_id", how="right")
    bio["dk_pos"] = bio["position"].map({"Guard": "G", "Forward": "F", "Center": "C"})
    rows = fp[["player_id", "fantasy_id", "name", "team_id", "probability_of_playing", "is_injured"]].copy()
    rows["season_id"] = NEW_SEASON
    rows["target"], rows["n_played"] = np.nan, 1
    return add_player_features(attach_history(rows, pg), bio.drop(columns="position"))


def add_player_features(rows, bio):
    rows = rows.merge(bio, on="player_id", how="left")
    start = pd.to_datetime(rows["season_id"].map(START_YEAR).astype(str) + "-10-20")
    rows["age"] = (start - pd.to_datetime(rows["birth_date"])).dt.days / 365.25
    rows["rookie"] = (rows["first_season"] == rows["season_id"]).astype(int)
    rows["undrafted"] = rows["pick"].isna().astype(int)
    rows["draft_bin"] = pd.cut(rows["pick"].fillna(99), DRAFT_BINS, labels=DRAFT_LABELS)
    rows["new_team"] = ((rows["prev_last_team"] != rows["team_id"]) & rows["prev_last_team"].notna()).astype(int)
    rows["size"] = rows["bref_pos"].map(SIZE).fillna(rows["dk_pos"].map({"G": 1, "F": 3, "C": 5}))
    rows["prev_missing"] = rows["prev_min"].isna().astype(int)
    return rows


def prior_minutes(rows, rookie_means):
    """Minuti "di partenza" di ogni giocatore, usati per misurare la concorrenza in rosa."""
    p = rows["prev_min"].copy()
    p = p.fillna(rows["prev2_min"])
    rk = rows["draft_bin"].astype(str).map(rookie_means)
    return p.fillna(rk).fillna(rookie_means.get("non scelto", 8.0))


def competition(rows):
    """Concorrenza tra compagni disponibili della stessa rosa di inizio stagione."""
    out = []
    for _, g in rows.groupby(["season_id", "team_id"]):
        pm, size, pos = g["prior"].values, g["size"].values, g["dk_pos"].values
        dist = np.abs(size[:, None] - size[None, :])
        sim = np.where(dist == 0, 1.0, np.where(dist <= 1, 0.5, 0.0))
        np.fill_diagonal(sim, 0)
        same = (pos[:, None] == pos[None, :]).astype(float)
        np.fill_diagonal(same, 0)
        out.append(pd.DataFrame({
            "comp_same": same @ pm,
            "comp_near": sim @ pm,
            "rank_same": 1 + (same * (pm[None, :] > pm[:, None])).sum(axis=1),
            "team_sum": pm.sum(),
            "roster_n": len(g),
        }, index=g.index))
    return rows.join(pd.concat(out))


# --------------------------------------------------------------------------- modelli

FEATS = ["prior", "prev_min", "prev_min_last20", "prev_gp", "prev_start", "prev_fp36", "prev_usage36",
         "prev_ts", "prev2_min", "prev_missing", "age", "pick", "undrafted", "rookie", "new_team", "size",
         "comp_same", "comp_near", "rank_same", "team_sum", "roster_n"]


def glm_frame(d, med):
    x = d[FEATS].copy()
    for c in ["prev_min", "prev_min_last20", "prev_gp", "prev_start", "prev_fp36", "prev_usage36", "prev_ts",
              "prev2_min", "age"]:
        x[c] = x[c].fillna(med[c])
    x["pick"] = x["pick"].fillna(61)
    x["log_rank"] = np.log(x["rank_same"])
    x["target"] = d["target"]
    return x


GLM_FORMULA = ("target ~ prior + prev_min_last20 + prev_start + prev_fp36 + prev_usage36 + prev_ts"
               " + prev_missing + age + I(age**2) + rookie:I(np.log(pick)) + undrafted + new_team"
               " + comp_same + comp_near + log_rank + team_sum + prior:new_team")


def close_240(d, pred, played_share):
    """Chiusura a 240: scala i minuti in modo che sum(quota partite giocate x minuti) = 240 per squadra."""
    out = pred.copy()
    for _, idx in d.groupby(["season_id", "team_id"]).groups.items():
        m, p = pred.loc[idx], played_share.loc[idx]
        for _ in range(20):                      # con il tetto serve qualche iterazione
            k = 240 / (p * m).sum()
            m = np.minimum(m * k, CAP)
            if abs(k - 1) < 1e-3:
                break
        out.loc[idx] = m
    return out


def mae(d, pred, mask=None):
    m = np.ones(len(d), bool) if mask is None else mask
    w = d["n_played"][m]
    return np.average(np.abs(d["target"][m] - pred[m]), weights=w), int(m.sum())


LGB_PARAMS = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_child_samples=40,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, verbose=-1, seed=7)
BANDS = [0, 10, 20, 30, 49]
BAND_LABELS = ["<10", "10-20", "20-30", ">30"]


def fit_predict(d, train, test):
    """Allena sugli avvii delle stagioni `train`, prevede l'avvio della stagione `test`."""
    d = d.copy()
    tr0 = d[d["season_id"].isin(train)]
    rookie_means = tr0[tr0["rookie"] == 1].groupby("draft_bin", observed=True)["target"].mean().astype(float)
    rookie_means.index = rookie_means.index.astype(str)
    d["prior"] = prior_minutes(d, rookie_means.to_dict())
    d = competition(d)
    d["played_share"] = d["n_played"] / FIRST_N
    tr, te = d[d["season_id"].isin(train)], d[d["season_id"] == test].copy()
    med = tr[FEATS].median(numeric_only=True)

    te["ingenuo"] = te["prior"]
    glm = smf.wls(GLM_FORMULA, data=glm_frame(tr, med), weights=tr["n_played"]).fit()
    te["glm"] = glm.predict(glm_frame(te, med)).clip(1, CAP).values
    model = lgb.train(LGB_PARAMS, lgb.Dataset(tr[FEATS], tr["target"], weight=tr["n_played"]), 400)
    te["lgbm"] = model.predict(te[FEATS]).clip(1, CAP)
    # quota di partite giocate tra le prime 10 (serve per chiudere a 240)
    ps = smf.ols("played_share ~ prior + prev_start + rookie + undrafted + log_rank + comp_same",
                 data=glm_frame(tr, med).assign(played_share=tr["played_share"])).fit()
    share = pd.Series(ps.predict(glm_frame(te, med)).clip(0.1, 1).values, index=te.index)
    te["lgbm + chiusura 240"] = close_240(te, te["lgbm"], share)
    return te, glm, model, rookie_means


METHODS = ["ingenuo", "glm", "lgbm", "lgbm + chiusura 240"]


def wmae(g, col):
    return np.average(np.abs(g["target"] - g[col]), weights=g["n_played"])


def wbias(g, col):
    return np.average(g[col] - g["target"], weights=g["n_played"])


def table(res, by):
    rows = []
    for key, g in res.groupby(by, observed=True):
        r = {by: key, "n": str(len(g))}
        r.update({m: wmae(g, m) for m in METHODS})
        rows.append(r)
    r = {by: "TOTALE", "n": str(len(res))}
    r.update({m: wmae(res, m) for m in METHODS})
    rows.append(r)
    return pd.DataFrame(rows).set_index(by)


def band_table(res, col):
    res = res.assign(fascia=pd.cut(res[col], BANDS, labels=BAND_LABELS))
    rows = []
    for key, g in res.groupby("fascia", observed=True):
        r = {"fascia": key, "n": str(len(g))}
        for m in METHODS:
            r[f"{m} MAE"] = wmae(g, m)
            r[f"{m} bias"] = wbias(g, m)
        rows.append(r)
    return pd.DataFrame(rows).set_index("fascia")


def main():
    db = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    pg, bio = load_games(db), load_bio(db)
    d = build(pg, bio)
    label = {s: f"{START_YEAR[s]}/{str(START_YEAR[s] + 1)[2:]}" for s in SEASONS}

    # validazione "in avanti": ogni stagione prevista allenando solo su quelle precedenti
    parts = []
    for test in TEST_SEASONS:
        train = [s for s in SEASONS if s < test and s != SEASONS[0]]
        te, glm, model, rookie_means = fit_predict(d, train, test)
        te["stagione"] = label[test]
        parts.append(te)
    res = pd.concat(parts)
    res["gruppo"] = np.select([res["rookie"] == 1, res["new_team"] == 1],
                              ["rookie", "cambiato squadra"], "stessa squadra")

    out = ["# Minuti di inizio stagione (generato da scripts/minuti_avvio.py)", "",
           f"Obiettivo: minuti medi (su 240) nelle partite giocate tra le prime {FIRST_N} della squadra.",
           "Validazione in avanti: ogni stagione e' prevista con modelli allenati solo sugli avvii precedenti",
           f"(prima stagione prevista: {label[TEST_SEASONS[0]]}). Errore medio assoluto in minuti, pesato per",
           "partite giocate. Bias = previsto - reale (positivo = sovrastima).", "",
           "## Per stagione", "", table(res, "stagione").to_markdown(floatfmt=".2f"), "",
           "## Per gruppo (tutte le stagioni)", "", table(res, "gruppo").to_markdown(floatfmt=".2f"), "",
           "## Per fascia di minuti REALI", "",
           "Attenzione: raggruppare per valore reale fa sembrare sovrastimati i bassi e sottostimati gli alti",
           "(regressione verso la media); la tabella per minuti PREVISTI e' quella che conta in uso.", "",
           band_table(res, "target").to_markdown(floatfmt="+.2f"), "",
           "## Per fascia di minuti PREVISTI (lgbm)", "",
           band_table(res, "lgbm").to_markdown(floatfmt="+.2f"), "",
           f"## Coefficienti GLM (ultimo allenamento, per il {label[TEST_SEASONS[-1]]})", "",
           pd.DataFrame({"coef": glm.params, "p": glm.pvalues}).to_markdown(floatfmt=".3f"), "",
           "## Rookie: minuti medi nelle prime 10 partite per fascia di draft", "",
           rookie_means.round(1).to_frame("minuti").to_markdown(), ""]
    imp = pd.Series(model.feature_importance("gain"), index=FEATS)
    out += ["## Importanza variabili (LightGBM, ultimo allenamento)", "",
            (imp / imp.sum()).sort_values(ascending=False).round(3).to_frame("quota").to_markdown(), ""]
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    res.to_parquet(ROOT / "data" / "minuti_avvio_validazione.parquet", index=False)


def predict_current():
    """Modello migliore (LightGBM) allenato su tutti gli avvii, applicato alle rose attuali.
    Salva in data/dunkest.sqlite, tabella stima_minuti_avvio."""
    db = sqlite3.connect(DB, timeout=30)
    pg, bio = load_games(db), load_bio(db)
    hist = build(pg, bio)
    cur = current_rows(db, pg)
    train = [s for s in SEASONS if s != SEASONS[0]]
    available = cur["probability_of_playing"].fillna(1) >= MIN_PLAY_PROB

    # 1) rosa con gli assenti di oggi esclusi; 2) tutti sani (minuti "di regime" anche per gli infortunati)
    now, _, _, _ = fit_predict(pd.concat([hist, cur[available]], ignore_index=True), train, NEW_SEASON)
    full, _, _, _ = fit_predict(pd.concat([hist, cur], ignore_index=True), train, NEW_SEASON)
    out = full[["fantasy_id", "player_id", "name", "team_id", "probability_of_playing", "rookie",
                "new_team", "prior", "lgbm"]].rename(columns={"lgbm": "minutes_all_healthy"})
    out = out.merge(now[["fantasy_id", "lgbm"]].rename(columns={"lgbm": "minutes"}), on="fantasy_id", how="left")
    out["player_id"] = out["player_id"].where(out["player_id"] > 0)
    out["created_at"] = pd.Timestamp.now().isoformat(timespec="seconds")
    out = out[["fantasy_id", "player_id", "name", "team_id", "minutes", "minutes_all_healthy",
               "probability_of_playing", "rookie", "new_team", "prior", "created_at"]]
    out.round(1).to_sql("stima_minuti_avvio", db, if_exists="replace", index=False)
    db.commit()
    print(f"salvati {len(out)} giocatori in stima_minuti_avvio")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--prevedi", action="store_true", help="minuti previsti per le rose attuali")
    if ap.parse_args().prevedi:
        predict_current()
    else:
        main()
