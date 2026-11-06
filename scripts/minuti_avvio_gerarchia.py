"""
Minuti di inizio stagione, versione con rating, gerarchia di squadra e minuti liberati.

Stessa validazione "in avanti" di minuti_avvio.py (ogni avvio dal 2021/22 al 2025/26 previsto
allenando solo sugli avvii precedenti), stesso obiettivo; si confrontano il modello attuale
(variabili originali) e quello nuovo con le variabili in piu':

  rating        rating di avvio del giocatore (rating_giocatori.py, solo stagioni precedenti);
                per chi non ha storico: prior per fascia di draft
  gerarchia     posizione per rating in squadra e nel ruolo (stesso ruolo / ruoli vicini),
                distacco dal migliore del ruolo, "unico del ruolo", distanza media dai compagni
  liberati      minuti per partita che avevano i giocatori della squadra l'anno prima e che non
                sono (piu') nella rosa di inizio stagione, in totale e per somiglianza di ruolo
  assenza       "stagione precedente mancante" divisa in: assente ma con minuti due anni prima
                (infortunato o fuori dai dati), mai visto prima (non rookie), piu' i minuti
                "sani" (anni con almeno 40 partite)

    python minuti_avvio_gerarchia.py   -> docs/minuti_avvio_gerarchia.md
"""

import sqlite3

import lightgbm as lgb
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from stima_dati import DB, ROOT
import minuti_avvio as base
from minuti_avvio import (SEASONS, NEW_SEASON, PREV_OF, START_YEAR, TEST_SEASONS, FIRST_N, CAP, SIZE,
                          DRAFT_BINS, DRAFT_LABELS, load_games, load_bio, build, current_rows,
                          competition, prior_minutes, glm_frame, close_240, wmae, LGB_PARAMS, BANDS,
                          BAND_LABELS)
from rating_giocatori import draft_prior

REPORT = ROOT / "docs" / "minuti_avvio_gerarchia.md"
RATING_START = ROOT / "data" / "rating_avvio.parquet"
RATING_SEASON = ROOT / "data" / "rating_stagione.parquet"

NEW_FEATS = ["rating", "rating_aff", "rank_team", "better_same", "better_near", "gap_same", "gap_near",
             "alone_same", "same_count", "dist_team", "vac_total", "vac_same", "vac_near", "vac_near_share",
             "abs_est", "abs_unknown", "healthy_min"]
FEATS2 = base.FEATS + NEW_FEATS


# --------------------------------------------------------------------------- contesto

def team_history(pg, bio):
    """Minuti per partita di squadra (su 240) di ogni giocatore in ogni (stagione, squadra)."""
    ng = pg.groupby(["season_id", "team_id"])["game_id"].nunique().rename("team_games")
    h = pg.groupby(["season_id", "team_id", "player_id"]).agg(tot=("min240", "sum")).reset_index()
    h = h.join(ng, on=["season_id", "team_id"])
    h["mpg_team"] = h["tot"] / h["team_games"]
    b = bio.drop_duplicates("player_id").set_index("player_id")
    size = b["bref_pos"].map(SIZE).fillna(b["dk_pos"].map({"G": 1, "F": 3, "C": 5}))
    h["size"] = h["player_id"].map(size)
    h["dk_pos"] = h["player_id"].map(b["dk_pos"])
    return h


def attach_rating(d, bio):
    """Rating di avvio; chi non ha storico negli ultimi 3 anni riceve il prior per fascia di draft."""
    st = pd.read_parquet(RATING_START)
    s = pd.read_parquet(RATING_SEASON)
    d = d.merge(st.rename(columns={"rating_avvio": "rating", "affidabilita": "rating_aff"})
                [["player_id", "season_id", "rating", "rating_aff"]], on=["player_id", "season_id"], how="left")
    miss = d["rating"].isna()
    for season in d.loc[miss, "season_id"].unique():
        pri, mean_all = draft_prior(s, bio, season)
        m = miss & (d["season_id"] == season)
        lab = pd.cut(d.loc[m, "pick"].fillna(99), DRAFT_BINS, labels=DRAFT_LABELS).astype(str)
        d.loc[m, "rating"] = lab.map(pri).fillna(mean_all).values
        d.loc[m, "rating_aff"] = 0.0
    return d


def hierarchy(d, hist):
    """Variabili di gerarchia (per rating) e minuti liberati, per ogni squadra e stagione."""
    out = []
    for (season, team), g in d.groupby(["season_id", "team_id"]):
        r, size, pos = g["rating"].values, g["size"].values, g["dk_pos"].values
        dist = np.abs(size[:, None] - size[None, :])
        sim = np.where(dist == 0, 1.0, np.where(dist <= 1, 0.5, 0.0))
        np.fill_diagonal(sim, 0)
        same = (pos[:, None] == pos[None, :]).astype(float)
        np.fill_diagonal(same, 0)
        better = r[None, :] > r[:, None]
        n = len(g)

        def best_other(mask):
            m = np.where(mask > 0, r[None, :], -np.inf).max(axis=1)
            return np.where(np.isfinite(m), m, np.nan)

        gap_same = r - best_other(same)
        gap_near = r - best_other(sim)
        alone = np.isnan(gap_same)
        # minuti liberati: chi era in squadra l'anno prima e non e' nella rosa di inizio stagione
        dep = hist[(hist["season_id"] == PREV_OF.get(season)) & (hist["team_id"] == team)
                   & ~hist["player_id"].isin(g["player_id"])]
        dm, dsz, dpos = dep["mpg_team"].values, dep["size"].values, dep["dk_pos"].values
        ddist = np.abs(size[:, None] - dsz[None, :])
        dsim = np.where(ddist == 0, 1.0, np.where(ddist <= 1, 0.5, 0.0))
        dsame = (pos[:, None] == dpos[None, :]).astype(float)
        vac_near = dsim @ dm if len(dm) else np.zeros(n)
        vac_same = dsame @ dm if len(dm) else np.zeros(n)
        prior = g["prior"].values
        kept_near = sim @ prior
        out.append(pd.DataFrame({
            "rank_team": 1 + better.sum(axis=1),
            "better_same": (same * better).sum(axis=1), "better_near": (sim * better).sum(axis=1),
            "gap_same": np.where(alone, r - 60, gap_same), "gap_near": np.where(np.isnan(gap_near), r - 60, gap_near),
            "alone_same": alone.astype(int), "same_count": same.sum(axis=1),
            "dist_team": r - (r.sum() - r) / max(n - 1, 1),
            "vac_total": dm.sum(), "vac_same": vac_same, "vac_near": vac_near,
            "vac_near_share": vac_near / (vac_near + kept_near + 1e-9),
        }, index=g.index))
    return d.join(pd.concat(out))


def absence_features(d):
    d["abs_est"] = ((d["prev_missing"] == 1) & (d["rookie"] == 0) & (d["prev2_min"] >= 10)).astype(int)
    d["abs_unknown"] = ((d["prev_missing"] == 1) & (d["rookie"] == 0) & d["prev2_min"].isna()).astype(int)
    full = d["prev_gp"] >= 40
    d["healthy_min"] = np.where(full, d["prev_min"], np.fmax(d["prev_min"], d["prev2_min"]))
    return d


def prepare(d, bio, hist, rookie_means):
    """Aggiunge a d tutte le variabili (originali + nuove). d deve contenere i dati storici e, per la
    previsione, le righe della stagione da prevedere."""
    d = d.copy()
    d["prior"] = prior_minutes(d, rookie_means)
    d = competition(d)
    d = attach_rating(d, bio)
    d = hierarchy(d, hist)
    d = absence_features(d)
    d["played_share"] = d["n_played"] / FIRST_N
    return d


# --------------------------------------------------------------------------- modelli

GLM_NEW = (base.GLM_FORMULA + " + rating + gap_near + better_near + alone_same + vac_near + vac_near_share"
           " + abs_est + abs_unknown + healthy_min")


def glm_frame2(d, med):
    x = glm_frame(d, med)          # include FEATS (originali) e target
    for c in NEW_FEATS:
        x[c] = d[c].fillna(med[c]) if c in med.index else d[c]
    return x


def dataset(tr, feats):
    c = lambda x: np.ascontiguousarray(x, dtype=float)       # copie contigue: LightGBM va in crash sulle viste
    return lgb.Dataset(c(tr[feats]), c(tr["target"]), weight=c(tr["n_played"]), feature_name=list(feats))


def fit_predict(d, bio, hist, train, test):
    d0 = d.copy()
    tr0 = d0[d0["season_id"].isin(train)]
    rookie_means = tr0[tr0["rookie"] == 1].groupby("draft_bin", observed=True)["target"].mean().astype(float)
    rookie_means.index = rookie_means.index.astype(str)
    # il prior (e quindi la concorrenza) dipende dai rookie_means del solo training: ricalcolato ogni volta
    d1 = prepare(d0[d0["season_id"].isin(list(train) + [test])], bio, hist, rookie_means.to_dict())
    tr, te = d1[d1["season_id"].isin(train)], d1[d1["season_id"] == test].copy()
    med = tr[FEATS2].median(numeric_only=True)

    te["ingenuo"] = te["prior"]
    # modello attuale
    glm = smf.wls(base.GLM_FORMULA, data=glm_frame(tr, med), weights=tr["n_played"]).fit()
    te["glm"] = glm.predict(glm_frame(te, med)).clip(1, CAP).values
    m0 = lgb.train(LGB_PARAMS, dataset(tr, base.FEATS), 400)
    te["lgbm"] = m0.predict(te[base.FEATS]).clip(1, CAP)
    # modello nuovo
    glm2 = smf.wls(GLM_NEW, data=glm_frame2(tr, med), weights=tr["n_played"]).fit()
    te["glm nuovo"] = glm2.predict(glm_frame2(te, med)).clip(1, CAP).values
    m1 = lgb.train(LGB_PARAMS, dataset(tr, FEATS2), 400)
    te["lgbm nuovo"] = m1.predict(te[FEATS2]).clip(1, CAP)
    # chiusura a 240 (stessa quota di partite giocate per entrambi)
    ps = smf.ols("played_share ~ prior + prev_start + rookie + undrafted + log_rank + comp_same",
                 data=glm_frame(tr, med).assign(played_share=tr["played_share"])).fit()
    share = pd.Series(ps.predict(glm_frame(te, med)).clip(0.1, 1).values, index=te.index)
    te["lgbm + 240"] = close_240(te, te["lgbm"], share)
    te["lgbm nuovo + 240"] = close_240(te, te["lgbm nuovo"], share)
    return te, (glm, glm2, m0, m1)


METHODS = ["ingenuo", "glm", "lgbm", "lgbm + 240", "glm nuovo", "lgbm nuovo", "lgbm nuovo + 240"]


def rank_corr(g, col):
    """Spearman medio per squadra fra previsto e reale: la classifica interna e' giusta?"""
    vals = []
    for _, t in g.groupby(["season_id", "team_id"]):
        if len(t) >= 6:
            vals.append(t[[col, "target"]].corr(method="spearman").iloc[0, 1])
    return np.nanmean(vals)


def table(res, by):
    rows = []
    for key, g in list(res.groupby(by, observed=True)) + [("TOTALE", res)]:
        r = {by: key, "n": str(len(g))}
        r.update({m: wmae(g, m) for m in METHODS})
        rows.append(r)
    return pd.DataFrame(rows).set_index(by)


def mask_table(res, masks):
    rows = []
    for name, m in masks.items():
        g = res[m]
        if len(g) == 0:
            continue
        r = {"gruppo": name, "n": str(len(g))}
        r.update({k: wmae(g, k) for k in ["ingenuo", "lgbm", "lgbm nuovo", "lgbm + 240", "lgbm nuovo + 240"]})
        rows.append(r)
    return pd.DataFrame(rows).set_index("gruppo")


def main():
    db = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    pg, bio = load_games(db), load_bio(db)
    d = build(pg, bio)
    hist = team_history(pg, bio)
    label = {s: f"{START_YEAR[s]}/{str(START_YEAR[s] + 1)[2:]}" for s in SEASONS}

    parts = []
    for test in TEST_SEASONS:
        train = [s for s in SEASONS if s < test and s != SEASONS[0]]
        te, models = fit_predict(d, bio, hist, train, test)
        te["stagione"] = label[test]
        parts.append(te)
        print("fatto", label[test], flush=True)
    res = pd.concat(parts)
    res["gruppo"] = np.select([res["rookie"] == 1, res["new_team"] == 1],
                              ["rookie", "cambiato squadra"], "stessa squadra")

    masks = {
        "tutti": np.ones(len(res), bool),
        "unico del ruolo (dk)": res["alone_same"] == 1,
        "primo per rating nel ruolo": (res["better_same"] == 0) & (res["alone_same"] == 0),
        "assente anno prima, con minuti 2 anni fa": res["abs_est"] == 1,
        "assente anno prima, mai visto": res["abs_unknown"] == 1,
        "pochi match anno prima (<40)": (res["prev_gp"] < 40) & (res["prev_missing"] == 0),
        "molti minuti liberati (vac_near>=40)": res["vac_near"] >= 40,
        "rookie": res["rookie"] == 1,
    }
    glm, glm2, m0, m1 = models
    imp = pd.Series(m1.feature_importance("gain"), index=FEATS2)
    rc = pd.DataFrame({m: {"Spearman medio per squadra": rank_corr(res, m)} for m in METHODS}).T

    out = ["# Minuti di inizio stagione: rating, gerarchia e minuti liberati", "",
           "Generato da `scripts/minuti_avvio_gerarchia.py`. Stessa validazione in avanti di `minuti_avvio.py`.",
           "`glm`/`lgbm` = variabili originali; `... nuovo` = con rating, gerarchia, minuti liberati, assenza.",
           "Errore medio assoluto in minuti (su 240), pesato per partite giocate.", "",
           "## Per stagione", "", table(res, "stagione").to_markdown(floatfmt=".2f"), "",
           "## Per gruppo", "", table(res, "gruppo").to_markdown(floatfmt=".2f"), "",
           "## Situazioni critiche", "", mask_table(res, masks).to_markdown(floatfmt=".2f"), "",
           "## Classifica interna alla squadra", "",
           "Spearman medio fra minuti previsti e reali dentro la stessa squadra e stagione.", "",
           rc.to_markdown(floatfmt=".3f"), "",
           "## Importanza variabili (LightGBM nuovo, ultimo allenamento)", "",
           (imp / imp.sum()).sort_values(ascending=False).round(3).to_frame("quota").to_markdown(), "",
           "## Coefficienti GLM nuovo (ultimo allenamento)", "",
           pd.DataFrame({"coef": glm2.params, "p": glm2.pvalues}).to_markdown(floatfmt=".3f"), ""]
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    res.to_parquet(ROOT / "data" / "minuti_avvio_gerarchia_validazione.parquet", index=False)


if __name__ == "__main__":
    main()
