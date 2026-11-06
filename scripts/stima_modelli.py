"""
Esperimento: confronta i metodi di stima del punteggio Dunkest di un giocatore in una partita
(dato che gioca). Allena sulle stagioni 2018/19-2023/24, sceglie i parametri sul 2024/25,
riallena su tutto fino al 2024/25 e misura sul 2025/26.

Metodi
  base_*      riferimenti semplici (media mobile del punteggio)
  glm_diretto regressione lineare sul punteggio
  lgbm_diretto LightGBM sul punteggio (media + quantili 10/50/90 per il range)
  glm_stat    GLM per minuti, ogni statistica (Poisson, minuti come esposizione) e % di tiro,
              poi simulazione Monte Carlo delle partite e formula Dunkest
  lgbm_stat   come glm_stat ma con LightGBM

Uscita: tabella a video + docs/stima_esperimento.md

    python stima_modelli.py
"""

import time
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import statsmodels.api as sm

from stima_dati import OUT as DATASET, ROOT, dunkest_score

warnings.filterwarnings("ignore")
REPORT = ROOT / "docs" / "stima_esperimento.md"

TRAIN, VALID, TEST = [1, 3, 5, 7, 9, 13], [19], [25]
MIN_HISTORY = 5                 # partite di storico minime per entrare nella valutazione
QS = [0.1, 0.5, 0.9]
N_SIM = 400
SEED = 7

# primitive simulate: conteggi per minuto (Poisson) e percentuali di tiro (binomiali)
COUNTS = ["fg2a", "tpa", "fta", "oreb", "dreb", "ast", "stl", "blk", "blka", "tov"]
PCTS = {"p2": ("fg2m", "fg2a"), "p3": ("tpm", "tpa"), "pft": ("ftm", "fta")}

NOT_FEATURES = {
    "game_id", "player_id", "season_id", "game_date", "team_id", "opponent_team_id",
    "opponent_side", "win", "minutes", "starter", "pdk", "pf", "position", "fp", "fp_base",
    "pts", "oreb", "dreb", "ast", "stl", "blk", "blka", "tov", "fgm", "fga", "ftm", "fta",
    "tpm", "tpa", "fg2m", "fg2a",
}
LGB_PARAMS = dict(learning_rate=0.05, num_leaves=31, min_child_samples=100,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
                  lambda_l2=1.0, verbose=-1, seed=SEED)


# --------------------------------------------------------------------------- dati

def prepare():
    d = pd.read_parquet(DATASET)
    d = d[d["career_games"] >= MIN_HISTORY].copy()
    d["fg2m"] = d["fgm"] - d["tpm"]
    d["fg2a"] = d["fga"] - d["tpa"]
    for tag in ("s", "l"):
        d[f"r{tag}_fg2a"] = d[f"r{tag}_fga"] - d[f"r{tag}_tpa"]
        d[f"r{tag}_fg2m"] = d[f"r{tag}_fgm"] - d[f"r{tag}_tpm"]
    d["days_since"] = d["days_since"].clip(upper=30)
    d["t_rest"] = d["t_rest"].clip(upper=10)
    d["o_rest"] = d["o_rest"].clip(upper=10)
    d["strength_diff"] = d["t_margin"] - d["o_margin"]
    feats = [c for c in d.columns if c not in NOT_FEATURES]
    return d, feats


def league_pcts(d):
    return {k: d[m].sum() / d[a].sum() for k, (m, a) in PCTS.items()}


def shrunk_pct(d, k, prior, n0=30):
    """% di tiro del giocatore (media esponenziale) ristretta verso la media di lega."""
    m, a = PCTS[k]
    made = d[f"rl_{m}"] * d["ml_min"]           # per partita
    att = d[f"rl_{a}"] * d["ml_min"]
    w = (att * 25).fillna(0)                     # ~25 partite di peso effettivo
    return ((made * 25).fillna(0) + n0 * prior) / (w + n0)


# --------------------------------------------------------------------------- metriche

def pinball(y, q, tau):
    e = y - q
    return np.mean(np.maximum(tau * e, (tau - 1) * e))


def metrics(name, y, mean, q10=None, q50=None, q90=None, crps=None):
    r = {"metodo": name, "MAE": np.mean(np.abs(y - mean)),
         "RMSE": np.sqrt(np.mean((y - mean) ** 2)), "bias": np.mean(mean - y)}
    if q10 is not None:
        r["MAE_mediana"] = np.mean(np.abs(y - q50))
        r["copertura_80"] = np.mean((y >= q10) & (y <= q90))
        r["ampiezza_80"] = np.mean(q90 - q10)
        r["pinball"] = np.mean([pinball(y, q, t) for q, t in zip((q10, q50, q90), QS)])
    if crps is not None:
        r["CRPS"] = np.mean(crps)
    return r


def crps_samples(samples, y):
    """CRPS da campioni (righe = osservazioni)."""
    x = np.sort(samples, axis=1)
    m = x.shape[1]
    term1 = np.mean(np.abs(x - y[:, None]), axis=1)
    w = 2 * np.arange(1, m + 1) - m - 1
    term2 = (x * w).sum(axis=1) / (m * m)
    return term1 - term2


# --------------------------------------------------------------------------- modelli componenti

def safe_log(x, eps=1e-3):
    return np.log(np.clip(x.fillna(x.median()), eps, None))


class GLMParts:
    """Minuti (OLS), conteggi (Poisson con offset log minuti), % tiro (binomiale), titolare, vittoria."""

    def _ctx(self, d):
        X = pd.DataFrame(index=d.index)
        X["home"] = d["home"]
        X["pace"] = (d["t_pace"].fillna(100) + d["o_pace"].fillna(100)) / 200
        X["allow"] = d["o_allow_pos"].fillna(d["o_allow_pos"].median()) / 30
        X["miss_usage"] = d["miss_usage"] / 10
        X["miss_same"] = d["miss_min_same_pos"] / 30
        X["new_team"] = d["new_team"]
        X["b2b"] = (d["t_rest"] <= 1).astype(float)
        X["G"] = (d["position"] == "G").astype(float)
        X["C"] = (d["position"] == "C").astype(float)
        return X

    def _min_X(self, d):
        X = self._ctx(d)
        for c in ["ms_min", "ml_min", "last_min", "ms_starter", "ml_starter", "last_starter"]:
            X[c] = d[c].fillna(d[c].median())
        X["missed_before"] = d["missed_before"].clip(upper=10)
        X["miss_min"] = d["miss_min"] / 30
        X["blowout"] = d["strength_diff"].abs().fillna(0) / 10
        X["early"] = (d["season_games"] < 5).astype(float)
        return sm.add_constant(X, has_constant="add")

    def _rate_X(self, d, stat):
        X = self._ctx(d)
        X["lr_l"] = safe_log(d[f"rl_{stat}"])
        X["lr_s"] = safe_log(d[f"rs_{stat}"])
        return sm.add_constant(X, has_constant="add")

    def fit(self, d):
        self.min = sm.OLS(d["minutes"], self._min_X(d)).fit()
        self.count = {s: sm.GLM(d[s], self._rate_X(d, s), family=sm.families.Poisson(),
                                offset=np.log(d["minutes"])).fit() for s in COUNTS}
        self.prior = league_pcts(d)
        self.pct = {}
        for k, (m, a) in PCTS.items():
            dd = d[d[a] > 0]
            X = sm.add_constant(pd.DataFrame({"logit": _logit(shrunk_pct(dd, k, self.prior[k]))}))
            self.pct[k] = sm.GLM(dd[m] / dd[a], X, family=sm.families.Binomial(),
                                 freq_weights=dd[a]).fit()
        Xs = self._starter_X(d)
        self.st = sm.GLM(d["starter"], Xs, family=sm.families.Binomial()).fit()
        self.win = sm.GLM(d["win"], self._win_X(d), family=sm.families.Binomial()).fit()
        return self

    def _starter_X(self, d):
        X = pd.DataFrame({c: d[c].fillna(0) for c in ["ms_starter", "ml_starter", "last_starter"]})
        X["miss_same"] = d["miss_min_same_pos"] / 30
        return sm.add_constant(X, has_constant="add")

    def _win_X(self, d):
        return sm.add_constant(pd.DataFrame({"diff": d["strength_diff"].fillna(0) / 10,
                                             "home": d["home"],
                                             "rest": d["t_rest"].fillna(2) - d["o_rest"].fillna(2)}),
                               has_constant="add")

    def predict(self, d):
        out = pd.DataFrame(index=d.index)
        out["min"] = self.min.predict(self._min_X(d)).clip(1, 48)
        for s in COUNTS:
            out[s] = self.count[s].predict(self._rate_X(d, s), offset=np.zeros(len(d)))  # per minuto
        for k in PCTS:
            X = sm.add_constant(pd.DataFrame({"logit": _logit(shrunk_pct(d, k, self.prior[k]))}),
                                has_constant="add")
            out[k] = self.pct[k].predict(X)
        out["p_st"] = self.st.predict(self._starter_X(d))
        out["p_win"] = self.win.predict(self._win_X(d))
        return out


class LGBParts:
    """Stessi componenti di GLMParts, stimati con LightGBM (early stopping sulla validazione)."""

    def __init__(self, feats, rounds=None):
        self.feats = feats
        self.rounds = rounds or {}

    def _train(self, key, obj, X, y, Xv=None, yv=None, w=None, wv=None, init=None, initv=None):
        params = dict(LGB_PARAMS, objective=obj)
        ds = lgb.Dataset(X, y, weight=w, init_score=init, free_raw_data=False)
        if key in self.rounds:
            return lgb.train(params, ds, num_boost_round=self.rounds[key])
        dv = lgb.Dataset(Xv, yv, weight=wv, init_score=initv, reference=ds)
        m = lgb.train(params, ds, num_boost_round=3000, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        self.rounds[key] = m.best_iteration
        return m

    def fit(self, d, v=None):
        X, Xv = d[self.feats], (v[self.feats] if v is not None else None)
        g = lambda df, c: df[c] if df is not None else None
        self.models = {}
        self.models["min"] = self._train("min", "regression", X, d["minutes"], Xv, g(v, "minutes"))
        for s in COUNTS:
            self.models[s] = self._train(
                s, "poisson", X, d[s], Xv, g(v, s),
                init=np.log(d["minutes"]), initv=np.log(v["minutes"]) if v is not None else None)
        for k, (m, a) in PCTS.items():
            dd = d[d[a] > 0]
            vv = v[v[a] > 0] if v is not None else None
            self.models[k] = self._train(
                k, "cross_entropy", dd[self.feats], dd[m] / dd[a],
                vv[self.feats] if vv is not None else None, (vv[m] / vv[a]) if vv is not None else None,
                w=dd[a], wv=vv[a] if vv is not None else None)
        self.models["p_st"] = self._train("p_st", "binary", X, d["starter"], Xv, g(v, "starter"))
        self.models["p_win"] = self._train("p_win", "binary", X, d["win"], Xv, g(v, "win"))
        return self

    def predict(self, d):
        X = d[self.feats]
        out = pd.DataFrame(index=d.index)
        out["min"] = np.clip(self.models["min"].predict(X), 1, 48)
        for s in COUNTS:
            out[s] = np.exp(self.models[s].predict(X, raw_score=True))   # per minuto
        for k in list(PCTS) + ["p_st", "p_win"]:
            out[k] = self.models[k].predict(X)
        return out


def _logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


# --------------------------------------------------------------------------- simulazione

class Simulator:
    """Simula partite dai parametri previsti e applica la formula Dunkest.

    minuti   = previsti + residuo pescato dai residui reali (per fascia di minuti previsti)
    forma    = fattore gamma comune a tutte le statistiche della partita (media 1, varianza v):
               introduce la correlazione "serata buona/serata storta" tra le statistiche
    conteggi = Poisson(tasso al minuto x minuti x forma); realizzati = Binomiale(tentativi, %)
    """

    def __init__(self, resid_pred, resid, v=0.1):
        self.edges = np.quantile(resid_pred, np.linspace(0, 1, 11)[1:-1])
        bins = np.digitize(resid_pred, self.edges)
        self.res = [np.asarray(resid[bins == b]) for b in range(10)]
        self.v = v

    def sample(self, p, n=N_SIM, seed=SEED, chunk=4000):
        rng = np.random.default_rng(seed)
        out = []
        for i in range(0, len(p), chunk):
            q = p.iloc[i:i + chunk]
            k = len(q)
            bins = np.digitize(q["min"].values, self.edges)
            res = np.empty((k, n))
            for b in np.unique(bins):
                idx = bins == b
                res[idx] = rng.choice(self.res[b], size=(idx.sum(), n))
            mins = np.clip(q["min"].values[:, None] + res, 0.5, 55)
            form = rng.gamma(1 / self.v, self.v, size=(k, n)) if self.v > 0 else 1.0
            lam = lambda s: q[s].values[:, None] * mins * form
            c = {s: rng.poisson(lam(s)) for s in COUNTS}
            fg2m = rng.binomial(c["fg2a"], q["p2"].values[:, None])
            tpm = rng.binomial(c["tpa"], q["p3"].values[:, None])
            ftm = rng.binomial(c["fta"], q["pft"].values[:, None])
            box = dict(pts=2 * fg2m + 3 * tpm + ftm, oreb=c["oreb"], dreb=c["dreb"], ast=c["ast"],
                       stl=c["stl"], blk=c["blk"], blka=c["blka"], tov=c["tov"],
                       fgm=fg2m + tpm, fga=c["fg2a"] + c["tpa"], ftm=ftm, fta=c["fta"], tpm=tpm,
                       starter=(rng.random((k, n)) < q["p_st"].values[:, None]).astype(float))
            win = (rng.random((k, n)) < q["p_win"].values[:, None]).astype(float)
            out.append(dunkest_score(box, win))
        return np.vstack(out)


def summarize(name, y, s):
    q10, q50, q90 = np.quantile(s, QS, axis=1)
    return metrics(name, y, s.mean(axis=1), q10, q50, q90, crps_samples(s, y))


def run_stat_model(name, parts_cls, tr, va, te, feats, log):
    t0 = time.time()
    mk = (lambda: parts_cls(feats)) if parts_cls is LGBParts else parts_cls
    # 1) allena su train, calibra la varianza della forma sulla validazione
    p1 = mk()
    p1 = p1.fit(tr, va) if parts_cls is LGBParts else p1.fit(tr)
    pv = p1.predict(va)
    ptr = p1.predict(tr)
    best = None
    sub = va.sample(min(len(va), 8000), random_state=SEED)
    for v in [0.0, 0.03, 0.06, 0.1, 0.15, 0.2, 0.3]:
        sim = Simulator(ptr["min"].values, (tr["minutes"] - ptr["min"]).values, v)
        s = sim.sample(pv.loc[sub.index])
        c = crps_samples(s, sub["fp"].values).mean()
        log(f"  {name}: forma v={v:.2f}  CRPS valid {c:.3f}")
        if best is None or c < best[1]:
            best = (v, c)
    # 2) riallena su train+valid, simula il test
    full = pd.concat([tr, va])
    p2 = parts_cls(feats, p1.rounds) if parts_cls is LGBParts else parts_cls()
    p2.fit(full)
    pf = p2.predict(full)
    sim = Simulator(pf["min"].values, (full["minutes"] - pf["min"]).values, best[0])
    s = sim.sample(p2.predict(te))
    log(f"  {name}: v scelta {best[0]}, {time.time() - t0:.0f}s")
    return summarize(name, te["fp"].values, s), p2


# --------------------------------------------------------------------------- metodi diretti

def direct_glm(tr, te):
    cols = ["ms_fp", "ml_fp", "last_fp", "ms_min", "ml_min", "ms_starter", "miss_usage",
            "miss_min_same_pos", "home", "strength_diff", "o_allow_pos", "o_pace", "t_pace",
            "missed_before", "new_team"]
    f = lambda d: sm.add_constant(d[cols].fillna(tr[cols].median()), has_constant="add")
    m = sm.OLS(tr["fp"], f(tr)).fit()
    mean = m.predict(f(te))
    sd = np.sqrt(m.scale)
    from scipy.stats import norm
    q = [mean + norm.ppf(t) * sd for t in QS]
    return metrics("glm_diretto", te["fp"].values, mean.values, *[x.values for x in q])


def direct_lgbm(tr, va, te, feats):
    res, models = {}, {}
    for key, obj, extra in [("mean", "regression", {}), *[(t, "quantile", {"alpha": t}) for t in QS]]:
        params = dict(LGB_PARAMS, objective=obj, **extra)
        ds = lgb.Dataset(tr[feats], tr["fp"])
        dv = lgb.Dataset(va[feats], va["fp"], reference=ds)
        m = lgb.train(params, ds, 3000, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        full = pd.concat([tr, va])
        m2 = lgb.train(params, lgb.Dataset(full[feats], full["fp"]), m.best_iteration)
        res[key] = m2.predict(te[feats])
        models[key] = m2
    return (metrics("lgbm_diretto", te["fp"].values, res["mean"], res[0.1], res[0.5], res[0.9]),
            models["mean"])


def baselines(tr, te):
    from scipy.stats import norm
    y = te["fp"].values
    out = []
    win_factor = 1 + 0.05 * tr["win"].mean()
    for name, col in [("base_media5", "ms_fp"), ("base_media25", "ml_fp")]:
        mean = te[col].fillna(tr[col].median()).values * win_factor
        sd = te["sd_fp"].fillna(tr["sd_fp"].median()).values
        out.append(metrics(name, y, mean, *[mean + norm.ppf(t) * sd for t in QS]))
    return out


# --------------------------------------------------------------------------- main

def main():
    lines = []

    def log(s):
        print(s, flush=True)
        lines.append(s)

    d, feats = prepare()
    tr, va, te = (d[d["season_id"].isin(s)] for s in (TRAIN, VALID, TEST))
    log(f"righe: train {len(tr):,}  valid {len(va):,}  test {len(te):,}  feature {len(feats)}")

    results = baselines(pd.concat([tr, va]), te)
    results.append(direct_glm(pd.concat([tr, va]), te))
    log("lgbm diretto...")
    r, lgb_direct = direct_lgbm(tr, va, te, feats)
    results.append(r)
    log("glm per statistica...")
    r, _ = run_stat_model("glm_stat", GLMParts, tr, va, te, feats, log)
    results.append(r)
    log("lgbm per statistica...")
    r, lgb_parts = run_stat_model("lgbm_stat", LGBParts, tr, va, te, feats, log)
    results.append(r)

    table = pd.DataFrame(results).set_index("metodo").round(3)
    log("\nTest 2025/26 (punteggio per partita, giocatori che giocano):")
    log(table.to_string())

    imp = pd.Series(lgb_direct.feature_importance("gain"), index=feats).sort_values(ascending=False)
    log("\nVariabili piu' importanti (lgbm diretto, media):")
    log((imp / imp.sum()).head(20).round(3).to_string())

    REPORT.write_text(
        "# Esperimento stima punteggio (generato da scripts/stima_modelli.py)\n\n```\n"
        + "\n".join(lines) + "\n```\n", encoding="utf-8")


if __name__ == "__main__":
    main()
