"""
Minuti di inizio stagione come BILANCIO DI SQUADRA diviso per ruoli (modello strutturale).

Idea: i 240 minuti della squadra si dividono in 5 "fasce di taglia" (1 = playmaker ... 5 = centro).
  - ogni giocatore ha un'affinita' con ogni fascia, kernel gaussiano sulla distanza di taglia (larghezza tau);
  - ogni giocatore ha un peso w = exp(theta . x) (minuti dell'anno prima, rating, eta', rookie, ...);
  - il budget di ogni fascia e' un mix fra quello medio di lega (appreso) e quello della stessa squadra
    l'anno prima (che include chi e' partito): (1 - lam) * g + lam * q;
  - un budget vale solo se in rosa c'e' chi puo' coprirlo: phi = D / (D + eps), poi si ririporta a 240;
  - minuti di un giocatore (quando gioca) = somma sulle fasce di  affinita' * budget * w / D, dove D e' la
    somma dei pesi (ognuno x quota di partite giocate) dei candidati della fascia.
Parametri (theta, tau, g, lam, eps) appresi minimizzando l'errore sui minuti (pesato per partite giocate).
Si valuta da solo e come variabile in piu' del LightGBM (con previsioni "fuori stagione" nel training).

    python minuti_avvio_quote.py            -> validazione, docs/minuti_avvio_quote.md
    python minuti_avvio_quote.py --prevedi  -> minuti previsti per le rose attuali (tabella stima_minuti_avvio)
                                               (prima: python rating_giocatori.py per aggiornare i rating)
"""

import lightgbm as lgb                 # PRIMA di pandas: altrimenti va in crash su questa macchina
import sqlite3

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy.optimize import minimize

from stima_dati import DB, ROOT
import minuti_avvio as base
import minuti_avvio_gerarchia as gh
from minuti_avvio import (SEASONS, NEW_SEASON, PREV_OF, START_YEAR, TEST_SEASONS, FIRST_N, CAP, load_games,
                          load_bio, build, glm_frame, close_240, wmae, LGB_PARAMS)

REPORT = ROOT / "docs" / "minuti_avvio_quote.md"
SLOTS = np.arange(1, 6)
TAU_Q = 0.7
ZCOLS = ["prior", "rating", "rookie", "rk_logpick", "undrafted", "age", "age2", "new_team", "prev_start",
         "abs_est", "abs_unknown", "healthy_min"]


# --------------------------------------------------------------------------- dati per squadra

def affinity(size, tau):
    a = np.exp(-(size[..., None] - SLOTS) ** 2 / (2 * tau ** 2))
    return a / a.sum(-1, keepdims=True)


def prev_slot_share(hist, season, team):
    """Quota di minuti per fascia della squadra l'anno prima (comprende chi e' partito)."""
    h = hist[(hist["season_id"] == PREV_OF.get(season)) & (hist["team_id"] == team)].dropna(subset=["size"])
    if h.empty:
        return np.full(5, 0.2)
    q = (affinity(h["size"].to_numpy(float), TAU_Q) * h["mpg_team"].to_numpy()[:, None]).sum(0)
    return q / q.sum()


def prep_frame(d1):
    d1 = d1.copy()
    d1["rk_logpick"] = d1["rookie"] * np.log(d1["pick"].fillna(61))
    d1["age2"] = (d1["age"].fillna(25) - 25) ** 2
    d1["age"] = d1["age"].fillna(25) - 25
    return d1


class Pack:
    """Array riempiti (squadre x giocatori) per un insieme di righe."""

    def __init__(self, d, hist, mu=None, sd=None):
        d = d.sort_values(["season_id", "team_id"])
        groups = list(d.groupby(["season_id", "team_id"]))
        T, N = len(groups), max(len(g) for _, g in groups)
        self.idx = [g.index.to_numpy() for _, g in groups]
        self.size, self.mask = np.full((T, N), 3.0), np.zeros((T, N))
        self.Z = np.zeros((T, N, len(ZCOLS)))
        self.ps, self.y, self.wt = np.full((T, N), 0.8), np.zeros((T, N)), np.zeros((T, N))
        self.q = np.zeros((T, 5))
        allz = d[ZCOLS].astype(float)
        self.mu = allz.mean() if mu is None else mu
        self.sd = (allz.std().replace(0, 1) if sd is None else sd)
        for t, ((season, team), g) in enumerate(groups):
            n = len(g)
            self.size[t, :n] = g["size"].fillna(3).to_numpy()
            self.mask[t, :n] = 1
            self.Z[t, :n] = ((g[ZCOLS].astype(float) - self.mu) / self.sd).fillna(0).to_numpy()
            self.ps[t, :n] = g["ps_hat"].to_numpy()
            self.y[t, :n] = g["target"].fillna(0).to_numpy()
            self.wt[t, :n] = g["n_played"].to_numpy()
            self.q[t] = prev_slot_share(hist, season, team)


NP = len(ZCOLS) + 1 + 5 + 1 + 1       # theta, log tau, b (softmax), logit lam, log eps


def unpack(p):
    F = len(ZCOLS)
    theta, tau = p[:F], np.exp(p[F])
    b = p[F + 1:F + 6]
    g = np.exp(b - b.max()); g /= g.sum()
    lam = 1 / (1 + np.exp(-p[F + 6]))
    eps = np.exp(p[F + 7])
    return theta, tau, g, lam, eps


def allocate(P, p):
    theta, tau, g, lam, eps = unpack(p)
    w = np.exp(np.clip(P.Z @ theta, -6, 6)) * P.mask
    a = affinity(P.size, tau) * P.mask[..., None]
    D = np.einsum("tn,tnk,tn->tk", P.ps, a, w)
    share = (1 - lam) * g + lam * P.q
    phi = D / (D + eps)
    B = share * phi
    B = 240 * B / B.sum(1, keepdims=True)
    m = np.einsum("tnk,tk,tn->tn", a, B / np.maximum(D, 1e-9), w)
    return m


def loss(p, P, ridge=1e-3):
    m = np.clip(allocate(P, p), 0, 48)
    return np.sum(P.wt * (m - P.y) ** 2) / P.wt.sum() + ridge * np.sum(p[:len(ZCOLS)] ** 2)


def fit_alloc(P):
    p0 = np.zeros(NP)
    p0[ZCOLS.index("prior")] = 0.8
    p0[ZCOLS.index("rating")] = 0.2
    p0[len(ZCOLS)] = np.log(0.8)                 # tau
    p0[len(ZCOLS) + 7] = np.log(5.0)             # eps
    r = minimize(loss, p0, args=(P,), method="L-BFGS-B", options=dict(maxiter=200, maxfun=6000))
    return r.x


def predict_alloc(P, p, index):
    m = np.clip(allocate(P, p), 1, CAP)
    out = pd.Series(np.nan, index=index)
    for t, ix in enumerate(P.idx):
        out.loc[ix] = m[t, :len(ix)]
    return out


# --------------------------------------------------------------------------- una stagione di test

def add_ps_hat(d1, train):
    med = d1[d1["season_id"].isin(train)][base.FEATS].median(numeric_only=True)
    tr = d1[d1["season_id"].isin(train)]
    ps = smf.ols("played_share ~ prior + prev_start + rookie + undrafted + log_rank + comp_same",
                 data=glm_frame(tr, med).assign(played_share=tr["played_share"])).fit()
    d1["ps_hat"] = ps.predict(glm_frame(d1, med)).clip(0.3, 1).values
    return d1, med


def alloc_fit_predict(d1, hist, train_seasons, target_idx):
    """Allena il bilancio sulle stagioni train e prevede le righe target_idx."""
    tr = d1[d1["season_id"].isin(train_seasons)]
    Ptr = Pack(tr, hist)
    p = fit_alloc(Ptr)
    Pte = Pack(d1.loc[target_idx], hist, Ptr.mu, Ptr.sd)
    return predict_alloc(Pte, p, d1.loc[target_idx].index), p


def run_season(d, bio, hist, train, test):
    tr0 = d[d["season_id"].isin(train)]
    rm = tr0[tr0["rookie"] == 1].groupby("draft_bin", observed=True)["target"].mean().astype(float)
    rm.index = rm.index.astype(str)
    d1 = gh.prepare(d[d["season_id"].isin(list(train) + [test])], bio, hist, rm.to_dict())
    d1 = prep_frame(d1)
    d1, med = add_ps_hat(d1, train)
    te_idx = d1.index[d1["season_id"] == test]
    # 1) bilancio da solo (allenato su tutte le stagioni di training)
    a_te, p_all = alloc_fit_predict(d1, hist, train, te_idx)
    # 2) previsioni "fuori stagione" per il training (serve al LightGBM che usa il bilancio come variabile)
    a_tr = pd.Series(np.nan, index=d1.index[d1["season_id"].isin(train)])
    for s in train:
        rest = [x for x in train if x != s]
        if len(rest) < 1:
            continue
        idx = d1.index[d1["season_id"] == s]
        a_tr.loc[idx], _ = alloc_fit_predict(d1, hist, rest, idx)
    d1["alloc"] = pd.concat([a_tr, a_te])
    tr, te = d1[d1["season_id"].isin(train)], d1[d1["season_id"] == test].copy()
    te["bilancio"] = te["alloc"]
    feats2 = gh.FEATS2
    trl = tr.dropna(subset=["alloc"])                 # la prima stagione di training non ha previsioni fuori stagione
    m0 = lgb.train(LGB_PARAMS, gh.dataset(tr, base.FEATS), 400)
    te["lgbm"] = m0.predict(te[base.FEATS].to_numpy(float)).clip(1, CAP)
    m1 = lgb.train(LGB_PARAMS, gh.dataset(tr, feats2), 400)
    te["lgbm nuovo"] = m1.predict(te[feats2].to_numpy(float)).clip(1, CAP)
    m2 = lgb.train(LGB_PARAMS, gh.dataset(trl, feats2 + ["alloc"]), 400)
    te["lgbm + bilancio"] = m2.predict(te[feats2 + ["alloc"]].to_numpy(float)).clip(1, CAP)
    te["media bil. + lgbm nuovo"] = 0.5 * te["bilancio"] + 0.5 * te["lgbm nuovo"]
    return te, p_all, m2, d1


METHODS = ["lgbm", "lgbm nuovo", "bilancio", "lgbm + bilancio", "media bil. + lgbm nuovo"]


def rank_corr(g, col):
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


def main():
    db = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    pg, bio = load_games(db), load_bio(db)
    d = build(pg, bio)
    hist = gh.team_history(pg, bio)
    label = {s: f"{START_YEAR[s]}/{str(START_YEAR[s] + 1)[2:]}" for s in SEASONS}
    parts = []
    for test in TEST_SEASONS:
        train = [s for s in SEASONS if s < test and s != SEASONS[0]]
        te, p, m2, d1 = run_season(d, bio, hist, train, test)
        te["stagione"] = label[test]
        parts.append(te)
        theta, tau, g, lam, eps = unpack(p)
        print(label[test], "tau %.2f lam %.2f eps %.1f g %s" % (tau, lam, eps, np.round(g, 2)), flush=True)
    res = pd.concat(parts)
    res["gruppo"] = np.select([res["rookie"] == 1, res["new_team"] == 1],
                              ["rookie", "cambiato squadra"], "stessa squadra")
    masks = {"tutti": np.ones(len(res), bool), "unico del ruolo (dk)": res["alone_same"] == 1,
             "assente anno prima, con minuti 2 anni fa": res["abs_est"] == 1,
             "molti minuti liberati (vac_near>=40)": res["vac_near"] >= 40, "rookie": res["rookie"] == 1}
    crit = pd.DataFrame([{"gruppo": k, "n": str(int(m.sum())), **{c: wmae(res[m], c) for c in METHODS}}
                         for k, m in masks.items()]).set_index("gruppo")
    rc = pd.DataFrame({m: {"Spearman medio per squadra": rank_corr(res, m)} for m in METHODS}).T
    theta, tau, g, lam, eps = unpack(p)
    out = ["# Minuti di inizio stagione: bilancio di squadra per ruoli", "",
           "Generato da `scripts/minuti_avvio_quote.py`. Stessa validazione in avanti.", "",
           "`bilancio` = solo il modello strutturale; `lgbm + bilancio` = LightGBM con il bilancio come variabile",
           "(previsioni fuori stagione sul training); `media` = 50% bilancio + 50% lgbm nuovo.", "",
           "## Per stagione", "", table(res, "stagione").to_markdown(floatfmt=".2f"), "",
           "## Per gruppo", "", table(res, "gruppo").to_markdown(floatfmt=".2f"), "",
           "## Situazioni critiche", "", crit.to_markdown(floatfmt=".2f"), "",
           "## Classifica interna alla squadra", "", rc.to_markdown(floatfmt=".3f"), "",
           f"## Parametri del bilancio (ultimo allenamento, per {label[TEST_SEASONS[-1]]})", "",
           f"tau (larghezza fascia) = {tau:.2f}; lambda (peso del budget dell'anno prima) = {lam:.2f}; "
           f"eps = {eps:.1f}; budget medio per fascia 1..5 = {np.round(g * 240, 1).tolist()}", "",
           pd.Series(theta, index=ZCOLS).round(2).to_frame("theta (pesi standardizzati)").to_markdown(), ""]
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    res.to_parquet(ROOT / "data" / "minuti_avvio_quote_validazione.parquet", index=False)


def predict_current(force=False):
    """Modello migliore ('lgbm + bilancio') allenato su tutti gli avvii, applicato alle rose attuali.
    Stesso formato di minuti_avvio.predict_current; salva in data/dunkest.sqlite, tabella stima_minuti_avvio
    (la tabella precedente resta in stima_minuti_avvio_vecchio, creata una sola volta)."""
    db = sqlite3.connect(DB, timeout=30)
    # a stagione iniziata la stima di avvio resta congelata: e' la partenza di minuti_in_stagione.py
    season_start = f"{START_YEAR[NEW_SEASON]}-10-01"
    started = db.execute("SELECT COUNT(*) FROM player_games WHERE game_date >= ?", (season_start,)).fetchone()[0]
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='stima_minuti_avvio'").fetchone()
    if started and exists and not force:
        print("stagione iniziata: stima_minuti_avvio congelata (usa --forza per rigenerarla)")
        return None
    pg, bio = load_games(db), load_bio(db)
    hist = build(pg, bio)
    cur = base.current_rows(db, pg)
    th = gh.team_history(pg, bio)
    train = [s for s in SEASONS if s != SEASONS[0]]
    available = cur["probability_of_playing"].fillna(1) >= base.MIN_PLAY_PROB

    # 1) rosa con gli assenti di oggi esclusi; 2) tutti sani (minuti "di regime" anche per gli infortunati)
    now = run_season(pd.concat([hist, cur[available]], ignore_index=True), bio, th, train, NEW_SEASON)[0]
    full = run_season(pd.concat([hist, cur], ignore_index=True), bio, th, train, NEW_SEASON)[0]
    out = full[["fantasy_id", "player_id", "name", "team_id", "probability_of_playing", "rookie",
                "new_team", "prior", "lgbm + bilancio"]].rename(columns={"lgbm + bilancio": "minutes_all_healthy"})
    out = out.merge(now[["fantasy_id", "lgbm + bilancio"]].rename(columns={"lgbm + bilancio": "minutes"}),
                    on="fantasy_id", how="left")
    out["player_id"] = out["player_id"].where(out["player_id"] > 0)
    out["created_at"] = pd.Timestamp.now().isoformat(timespec="seconds")
    out = out[["fantasy_id", "player_id", "name", "team_id", "minutes", "minutes_all_healthy",
               "probability_of_playing", "rookie", "new_team", "prior", "created_at"]]
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='stima_minuti_avvio'").fetchone()
    backup = db.execute("SELECT 1 FROM sqlite_master WHERE name='stima_minuti_avvio_vecchio'").fetchone()
    if exists and not backup:
        db.execute("CREATE TABLE stima_minuti_avvio_vecchio AS SELECT * FROM stima_minuti_avvio")
    out.round(1).to_sql("stima_minuti_avvio", db, if_exists="replace", index=False)
    db.commit()
    print(f"salvati {len(out)} giocatori in stima_minuti_avvio")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--prevedi", action="store_true", help="minuti previsti per le rose attuali")
    ap.add_argument("--forza", action="store_true", help="rigenera anche a stagione iniziata")
    a = ap.parse_args()
    if a.prevedi:
        predict_current(a.forza)
    else:
        main()
