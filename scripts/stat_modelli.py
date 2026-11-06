"""
Confronto dei modelli delle singole statistiche sul 2025/26 (partita per partita, minuti reali come
esposizione: qui si valuta il tasso al minuto, non la previsione dei minuti).

  base   tasso del giocatore ristretto verso la media di ruolo x minuti
  glmm   GLMM glmmTMB (scripts/stat_glmm.R -> data/stat_glmm_pred.parquet)
  lgbm   LightGBM Poisson con offset log(minuti) (percentuali: cross-entropy pesata sui tentativi)

Metriche conteggi: MAE, devianza Poisson media, copertura dell'intervallo 10-90%
(reale vs attesa dal modello: con dati discreti l'attesa non e' esattamente 80%).
Metriche percentuali: log-loss per tentativo, errore sui realizzati.

    python stat_modelli.py            -> docs/stat_modelli.md
"""

import lightgbm as lgb      # prima di pandas/pyarrow: su Windows altrimenti conflitto OpenMP
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

from stat_dati import COUNTS, OUT as DATASET, PCTS, ROOT

REPORT = ROOT / "docs" / "stat_modelli.md"
GLMM_PRED = ROOT / "data" / "stat_glmm_pred.parquet"
GLMM_INFO = ROOT / "data" / "stat_glmm_info.csv"
GLMM_COEF = ROOT / "data" / "stat_glmm_coef.csv"
PRED_TEST = ROOT / "data" / "stat_pred_test.parquet"       # previsioni lgbm/base sul test, per altre verifiche
TRAIN, VALID, TEST = [1, 3, 5, 7, 9, 13], [19], [25]
CONTEXT = ["dev", "pace", "home", "b2b", "miss_usage10", "miss_same30", "new_team", "z_age", "z_height_cm",
           "z_weight_kg", "pos_code", "career_games", "season_games", "ms_min", "ml_min", "missed_before",
           "t_rating", "o_rating"]
LGB_PARAMS = dict(learning_rate=0.05, num_leaves=31, min_child_samples=200, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=7, num_threads=0)


def features(stat=None):
    f = CONTEXT + [f"lr_{c}" for c in COUNTS] + [f"form_{c}" for c in COUNTS] + [f"lgt_{k}" for k in PCTS]
    return f + ([f"loa_{stat}"] if stat in COUNTS else [])


def train_lgb(obj, X, y, Xv, yv, w=None, wv=None, init=None, initv=None):
    p = dict(LGB_PARAMS, objective=obj)
    ds = lgb.Dataset(X, y, weight=w, init_score=init)
    m = lgb.train(p, ds, 2000, valid_sets=[lgb.Dataset(Xv, yv, weight=wv, init_score=initv, reference=ds)],
                  callbacks=[lgb.early_stopping(50, verbose=False)])
    return m.best_iteration, m


def calibrate(family, y, offset, d, w=None):
    """Fattore di forza squadre sopra LightGBM: GLM con la previsione come offset, stimato sulla
    validazione (fuori campione per il modello allenato sulle stagioni precedenti)."""
    X = sm.add_constant(d[["o_rating", "t_rating"]].fillna(0), has_constant="add")
    return sm.GLM(y, X, family=family, offset=offset, freq_weights=w).fit().params


def apply_cal(params, d):
    return params["const"] + params["o_rating"] * d["o_rating"].fillna(0) + params["t_rating"] * d["t_rating"].fillna(0)


def nb2_alpha(y, mu):
    """Sovradispersione NB2 (var = mu + alpha mu^2) col metodo dei momenti; 0 = Poisson."""
    return max(0.0, float(np.sum((y - mu) ** 2 - mu) / np.sum(mu ** 2)))


def dist(family, mu, disp):
    """Distribuzione scipy per famiglia glmmTMB (nbinom2: disp = theta; nbinom1: disp = phi)."""
    if family == "nbinom2" and disp and np.isfinite(disp):
        return stats.nbinom(disp, disp / (disp + mu))
    if family == "nbinom1" and disp and disp > 0:
        return stats.nbinom(mu / disp, 1 / (1 + disp))
    return stats.poisson(mu)


def count_metrics(y, mu, family, disp):
    D = dist(family, mu, disp)
    q10, q90 = D.ppf(0.1), D.ppf(0.9)
    inside = (y >= q10) & (y <= q90)
    expected = D.cdf(q90) - D.cdf(q10 - 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        dev = 2 * (np.where(y > 0, y * np.log(y / mu), 0) - (y - mu))
    return {"MAE": np.mean(np.abs(y - mu)), "devianza": np.mean(dev), "bias": np.mean(mu - y),
            "copertura": np.mean(inside), "copertura attesa": np.mean(expected)}


def pct_metrics(made, att, p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    ll = -(made * np.log(p) + (att - made) * np.log(1 - p)).sum() / att.sum()
    return {"log-loss": ll, "MAE realizzati": np.mean(np.abs(made - p * att)),
            "bias %": 100 * (np.sum(p * att) - made.sum()) / att.sum()}


def main():
    d = pd.read_parquet(DATASET)
    d["pos_code"] = d["position"].map({"G": 0, "F": 1, "C": 2})
    tr, va, te = (d[d["season_id"].isin(s)].copy() for s in (TRAIN, VALID, TEST))
    full = pd.concat([tr, va])

    glmm = pd.read_parquet(GLMM_PRED) if GLMM_PRED.exists() else None
    if glmm is not None:
        glmm["player_id"] = glmm["player_id"].astype(int)
        te = te.merge(glmm, on=["game_id", "player_id"], how="left")
    info = pd.read_csv(GLMM_INFO).set_index("target") if GLMM_INFO.exists() else pd.DataFrame()

    out = ["# Modelli delle singole statistiche (generato da scripts/stat_modelli.py)", "",
           f"Test: 2025/26, {len(te):,} partite giocatore, minuti reali come esposizione.",
           "Copertura = quota di partite con valore reale nell'intervallo 10-90% del modello; va confrontata con",
           "la copertura attesa (dati discreti: non e' esattamente 80%).", ""]
    rows, cal_rows = [], []
    for c in COUNTS:
        f = features(c)
        it, m_tr = train_lgb("poisson", tr[f], tr[c], va[f], va[c], init=np.log(tr["minutes"]),
                             initv=np.log(va["minutes"]))
        off_va = m_tr.predict(va[f], raw_score=True, num_iteration=it) + np.log(va["minutes"])
        cal = calibrate(sm.families.Poisson(), va[c], off_va, va)
        cal_rows.append({"statistica": c, **cal.round(4).to_dict()})
        m = lgb.train(dict(LGB_PARAMS, objective="poisson"),
                      lgb.Dataset(full[f], full[c], init_score=np.log(full["minutes"])), it)
        mu_full = np.exp(m.predict(full[f], raw_score=True)) * full["minutes"]
        alpha = nb2_alpha(full[c].values, mu_full.values)
        te[f"lgbm_{c}"] = np.exp(m.predict(te[f], raw_score=True)) * te["minutes"]
        te[f"cal_{c}"] = te[f"lgbm_{c}"] * np.exp(apply_cal(cal, te))
        te[f"base_{c}"] = np.exp(te[f"lr_{c}"]) * te["minutes"]
        alpha_base = nb2_alpha(full[c].values, (np.exp(full[f"lr_{c}"]) * full["minutes"]).values)
        y = te[c].values
        for name, col, fam, disp in [
                ("base", f"base_{c}", "nbinom2", 1 / alpha_base if alpha_base > 0 else None),
                ("glmm", f"mu_{c}", info.loc[c, "family"] if c in info.index else None,
                 info.loc[c, "dispersion"] if c in info.index else None),
                ("lgbm", f"lgbm_{c}", "nbinom2", 1 / alpha if alpha > 0 else None),
                ("lgbm+cal", f"cal_{c}", "nbinom2", 1 / alpha if alpha > 0 else None)]:
            if col not in te or te[col].isna().all():
                continue
            ok = te[col].notna().values
            r = {"statistica": c, "modello": name}
            r.update(count_metrics(y[ok], te[col].values[ok], fam, disp))
            rows.append(r)
        print(f"{c}: fatto", flush=True)
    res = pd.DataFrame(rows)
    out += ["## Conteggi", "", res.pivot(index="statistica", columns="modello", values="MAE")
            .loc[COUNTS].to_markdown(floatfmt=".3f"), "",
            "Devianza Poisson media (piu' bassa = meglio):", "",
            res.pivot(index="statistica", columns="modello", values="devianza").loc[COUNTS]
            .to_markdown(floatfmt=".3f"), "",
            "Copertura reale / attesa dell'intervallo 10-90%:", "",
            res.assign(cop=res["copertura"].map("{:.1%}".format) + " / " + res["copertura attesa"]
                       .map("{:.1%}".format)).pivot(index="statistica", columns="modello", values="cop")
            .loc[COUNTS].to_markdown(), ""]

    prow = []
    for k, (made, att, _) in PCTS.items():
        f = features()
        trk, vak, fk = tr[tr[att] > 0], va[va[att] > 0], full[full[att] > 0]
        it, m_tr = train_lgb("cross_entropy", trk[f], trk[made] / trk[att], vak[f], vak[made] / vak[att],
                             w=trk[att], wv=vak[att])
        pv = np.clip(m_tr.predict(vak[f], num_iteration=it), 1e-4, 1 - 1e-4)
        cal = calibrate(sm.families.Binomial(), vak[made] / vak[att], np.log(pv / (1 - pv)), vak, w=vak[att])
        cal_rows.append({"statistica": k, **cal.round(4).to_dict()})
        m = lgb.train(dict(LGB_PARAMS, objective="cross_entropy"),
                      lgb.Dataset(fk[f], fk[made] / fk[att], weight=fk[att]), it)
        te[f"lgbm_p_{k}"] = m.predict(te[f])
        lp = np.log(np.clip(te[f"lgbm_p_{k}"], 1e-4, 1 - 1e-4) / (1 - np.clip(te[f"lgbm_p_{k}"], 1e-4, 1 - 1e-4)))
        te[f"cal_p_{k}"] = 1 / (1 + np.exp(-(lp + apply_cal(cal, te))))
        tk = te[te[att] > 0]
        for name, p in [("base", 1 / (1 + np.exp(-tk[f"lgt_{k}"]))), ("glmm", tk.get(f"p_{k}")),
                        ("lgbm", m.predict(tk[f])), ("lgbm+cal", tk[f"cal_p_{k}"])]:
            if p is None or pd.isna(p).all():
                continue
            r = {"percentuale": k, "modello": name}
            r.update(pct_metrics(tk[made].values, tk[att].values, np.asarray(p)))
            prow.append(r)
    out += ["## Percentuali di tiro", "", pd.DataFrame(prow).to_markdown(index=False, floatfmt=".4f"), "",
            "## Calibrazione forza squadre (stimata sul 2024/25)", "",
            "Fattore moltiplicativo exp(const + g x rating avversario + d x rating squadra) sulla previsione LightGBM",
            "(percentuali: sul logit). Rating in punti di margine attesi.", "",
            pd.DataFrame(cal_rows).to_markdown(index=False, floatfmt=".4f"), ""]

    if GLMM_COEF.exists():
        cf = pd.read_csv(GLMM_COEF)
        el = cf[cf["term"] == "dev"].set_index("target")[["estimate", "se"]]
        el["esponente minuti (1+beta)"] = 1 + el["estimate"]
        out += ["## Elasticita' ai minuti (GLMM)", "",
                "stat proporzionale a minuti^(1+beta) a parita' di tutto il resto; beta < 0 = meno che proporzionale.",
                "", el.to_markdown(floatfmt=".3f"), "",
                "## Famiglie e dispersione (GLMM)", "", info.to_markdown(floatfmt=".3f"), ""]
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    keep = ["game_id", "player_id", "team_id", "opponent_team_id", "minutes", "starter", "win", "t_rating",
            "o_rating", "fg2m", "tpm", "ftm"] + COUNTS + [c for c in te.columns if c.startswith(("lgbm_", "base_", "cal_"))]
    te[keep].to_parquet(PRED_TEST, index=False)


if __name__ == "__main__":
    main()
