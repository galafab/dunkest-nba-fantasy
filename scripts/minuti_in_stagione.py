"""
Minuti IN STAGIONE: aggiorna la stima di inizio stagione dopo ogni partita giocata.

Per ogni giocatore e partita della stagione (che il giocatore gioca) si prevedono i minuti della partita
da: stima di inizio stagione (walk-forward, modello 'lgbm + bilancio'), minuti delle partite gia' giocate
(ultima, ultime 3, ultime 5, media esponenziale, media stagionale, quota di partenze), numero di partite
giocate dal giocatore e dalla squadra, minuti "di regime" dei compagni assenti oggi.

Confronto, per numero di partite gia' giocate k (0 = prima partita):
  inizio       stima di inizio stagione ferma (come oggi in produzione)
  ewm          media esponenziale dei minuti della stagione (con ripiego sulla stima per k = 0)
  bayes        (c * inizio + somma minuti) / (c + k), c scelto sul training
  lgbm         LightGBM con tutte le variabili sopra, allenato sulle stagioni precedenti

Validazione in avanti: ogni stagione di test prevista allenando solo sulle precedenti.

    python minuti_in_stagione.py            -> validazione, docs/minuti_in_stagione.md
    python minuti_in_stagione.py --prevedi  -> minuti per la prossima partita delle rose attuali, tabella
                                               stima_minuti_stagione (usata da stima_partita.py). Partenza:
                                               stima_minuti_avvio, congelata dalla prima partita della stagione.
"""

import lightgbm as lgb                 # PRIMA di pandas (crash su questa macchina)
import sqlite3

import numpy as np
import pandas as pd

from stima_dati import DB, ROOT
from minuti_avvio import load_games, LGB_PARAMS, CAP, START_YEAR, NEW_SEASON, MIN_PLAY_PROB

REPORT = ROOT / "docs" / "minuti_in_stagione.md"
START_FILE = ROOT / "data" / "minuti_avvio_quote_validazione.parquet"
START_COL = "lgbm + bilancio"
MIN_RUN = 2                           # assenze consecutive minime per contare come "assente" (infortunio)
ROTATION = 12.0                       # minuti di regime oltre i quali un compagno assente "pesa"
TEST_SEASONS = [9, 13, 19, 25]        # 2022/23 ... 2025/26 (la 2021/22 serve solo da training)
KB = [(0, 0), (1, 1), (2, 2), (3, 4), (5, 9), (10, 19), (20, 39), (40, 99)]
KB_LABELS = ["0 (prima)", "1", "2", "3-4", "5-9", "10-19", "20-39", "40+"]
SEASON_START = f"{START_YEAR[NEW_SEASON]}-10-01"     # partite da questa data = stagione in corso
FEATS = ["start", "k", "t", "played_frac", "last1", "last3", "last5", "ewm5", "ewm15", "season_mean",
         "starter5", "miss_min", "miss_n", "home"]


# --------------------------------------------------------------------------- dataset

def season_games(pg, start):
    """Una riga per giocatore della rosa di inizio stagione e partita giocata, con le variabili "prima della partita"."""
    s = start[["season_id", "team_id", "player_id", START_COL]].rename(columns={START_COL: "start"})
    g = pg.merge(s, on=["season_id", "team_id", "player_id"])         # solo rosa di inizio stagione, stessa squadra
    g = g.sort_values(["season_id", "player_id", "game_date", "game_id"]).reset_index(drop=True)
    grp = g.groupby(["season_id", "player_id"])["min240"]
    g["k"] = grp.cumcount()
    sh = grp.shift(1)
    g["last1"] = sh
    g["last3"] = sh.groupby([g["season_id"], g["player_id"]]).transform(lambda x: x.rolling(3, min_periods=1).mean())
    g["last5"] = sh.groupby([g["season_id"], g["player_id"]]).transform(lambda x: x.rolling(5, min_periods=1).mean())
    g["ewm5"] = sh.groupby([g["season_id"], g["player_id"]]).transform(lambda x: x.ewm(halflife=3, ignore_na=True).mean())
    g["ewm15"] = sh.groupby([g["season_id"], g["player_id"]]).transform(lambda x: x.ewm(halflife=10, ignore_na=True).mean())
    g["season_mean"] = sh.groupby([g["season_id"], g["player_id"]]).transform(lambda x: x.expanding().mean())
    g["starter5"] = (g.groupby(["season_id", "player_id"])["starter"].shift(1)
                     .groupby([g["season_id"], g["player_id"]]).transform(lambda x: x.rolling(5, min_periods=1).mean()))
    g["cum_sum"] = grp.cumsum() - g["min240"]                       # somma dei minuti prima di questa partita
    # partite della squadra fino ad ora
    tg = g[["season_id", "team_id", "game_id", "game_date"]].drop_duplicates().sort_values(["game_date", "game_id"])
    tg["t"] = tg.groupby(["season_id", "team_id"]).cumcount()
    g = g.merge(tg[["season_id", "team_id", "game_id", "t"]], on=["season_id", "team_id", "game_id"])
    g["played_frac"] = g["k"] / g["t"].where(g["t"] > 0)
    # compagni di regime assenti in questa partita. Per somigliare alla lista infortuni usata in produzione:
    # contano solo assenze di almeno MIN_RUN partite consecutive, e non dopo il passaggio ad altra squadra
    roster = s[s["start"] >= ROTATION]
    pres = g[["season_id", "team_id", "game_id", "player_id"]]
    last_date = pg.groupby(["season_id", "player_id", "team_id"])["game_date"].max()
    rows = []
    for (season, team), rg in roster.groupby(["season_id", "team_id"]):
        tgs = tg[(tg["season_id"] == season) & (tg["team_id"] == team)]
        games, dates = tgs["game_id"].to_numpy(), tgs["game_date"].to_numpy()
        played = pres[(pres["season_id"] == season) & (pres["team_id"] == team)]
        mat = pd.crosstab(played["game_id"], played["player_id"]).reindex(index=games, columns=rg["player_id"],
                                                                         fill_value=0)
        absent = (mat.to_numpy() == 0)
        valid = np.zeros_like(absent)
        for j, pid in enumerate(rg["player_id"]):
            a = absent[:, j].copy()
            ld = last_date.get((season, pid))
            here = ld.get(team) if ld is not None else None
            if ld is not None and here is not None and (ld.drop(team) > here).any():
                a &= dates <= here                                   # ceduto: dopo l'ultima partita qui non e' "assente"
            run_id = np.cumsum(np.r_[True, a[1:] != a[:-1]])
            run_len = pd.Series(a).groupby(run_id).transform("size").to_numpy()
            valid[:, j] = a & (run_len >= MIN_RUN)
        st_ = rg["start"].to_numpy()
        rows.append(pd.DataFrame({"season_id": season, "team_id": team, "game_id": games,
                                  "miss_min": valid @ st_, "miss_n": valid.sum(1),
                                  "miss_min_raw": absent @ st_}))
    g = g.merge(pd.concat(rows), on=["season_id", "team_id", "game_id"], how="left")
    g["home"] = (g["opponent_side"] == "visitor").astype(int) if "opponent_side" in g else 0
    for c in ["miss_min", "miss_n", "miss_min_raw"]:
        g[c] = g[c].fillna(0)
    return g


def games_with_home(db):
    pg = load_games(db)
    pg = pg.merge(pd.read_sql("SELECT game_id, home_team_id FROM games", db), on="game_id", how="left")
    pg["opponent_side"] = np.where(pg["team_id"] == pg["home_team_id"], "visitor", "home")   # come in minuti_avvio
    return pg


# --------------------------------------------------------------------------- modelli

def bayes_pred(d, c):
    return (c * d["start"] + d["cum_sum"]) / (c + d["k"])


FEATS0 = ["start", "t", "miss_min", "miss_n", "home"]      # prima partita: niente minuti della stagione
LGB0_PARAMS = dict(LGB_PARAMS, num_leaves=7, min_child_samples=60)
K0_METHOD = "lgbm"                                        # modello scelto per la prima partita (vedi validazione)


def _X(d, feats):
    return np.ascontiguousarray(d[feats].to_numpy(float))


def _lin0_design(d):
    s = d["start"].to_numpy(float)
    return np.column_stack([np.ones(len(d)), s, s ** 2, d["miss_min"].to_numpy(float), d["t"].to_numpy(float)])


def fit_models(train):
    """Due modelli: prima partita (k = 0) e partite successive (k >= 1), allenati su righe separate."""
    tr0, tr1 = train[train["k"] == 0], train[train["k"] >= 1]
    y = lambda d: np.ascontiguousarray(d["min240"].to_numpy(float))
    return {
        "resto": lgb.train(LGB_PARAMS, lgb.Dataset(_X(tr1, FEATS), y(tr1), feature_name=FEATS), 400),
        "k0 lgbm": lgb.train(LGB0_PARAMS, lgb.Dataset(_X(tr0, FEATS0), y(tr0), feature_name=FEATS0), 200),
        "k0 lineare": np.linalg.lstsq(_lin0_design(tr0), y(tr0), rcond=None)[0],
    }


def predict_models(models, d, k0=K0_METHOD):
    rest = models["resto"].predict(_X(d, FEATS))
    first = (models["k0 lgbm"].predict(_X(d, FEATS0)) if k0 == "lgbm"
             else _lin0_design(d) @ models["k0 lineare"])
    return np.clip(np.where(d["k"].to_numpy() == 0, first, rest), 0, CAP)


def fit_predict(train, test):
    out = test[["season_id", "player_id", "k", "min240", "start"]].copy()
    out["inizio"] = test["start"]
    out["ewm"] = test["ewm5"].fillna(test["start"])
    grid = [2, 4, 6, 8, 12, 16, 24, 32, 48]
    best = min(grid, key=lambda c: np.abs(train["min240"] - bayes_pred(train, c)).mean())
    out["bayes"] = bayes_pred(test, best)
    m = lgb.train(LGB_PARAMS, lgb.Dataset(_X(train, FEATS), np.ascontiguousarray(train["min240"].to_numpy(float)),
                                          feature_name=FEATS), 400)
    out["lgbm"] = m.predict(_X(test, FEATS)).clip(0, CAP)
    models = fit_models(train)
    out["separati (k0 lgbm)"] = predict_models(models, test, "lgbm")
    out["separati (k0 lineare)"] = predict_models(models, test, "lineare")
    return out, best, models["resto"]


METHODS = ["inizio", "ewm", "bayes", "lgbm", "separati (k0 lgbm)", "separati (k0 lineare)"]


def k0_bias(res):
    """Prima partita: media reale e previsioni per fascia della stima di inizio stagione."""
    k0 = res[res["k"] == 0].copy()
    k0["fascia inizio"] = pd.cut(k0["start"], [0, 10, 20, 30, 45]).astype(str)
    cols = ["min240", "inizio", "lgbm", "separati (k0 lgbm)", "separati (k0 lineare)"]
    t = k0.groupby("fascia inizio")[cols].mean()
    t.insert(0, "n", k0.groupby("fascia inizio").size())
    return t.rename(columns={"min240": "reale"})


def by_k(res):
    rows = []
    for (lo, hi), lab in zip(KB, KB_LABELS):
        g = res[(res["k"] >= lo) & (res["k"] <= hi)]
        rows.append({"partite giocate": lab, "n": str(len(g)),
                     **{m: np.abs(g["min240"] - g[m]).mean() for m in METHODS}})
    rows.append({"partite giocate": "TOTALE", "n": str(len(res)),
                 **{m: np.abs(res["min240"] - res[m]).mean() for m in METHODS}})
    return pd.DataFrame(rows).set_index("partite giocate")


def main():
    db = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    pg = games_with_home(db)
    start = pd.read_parquet(START_FILE)
    g = season_games(pg, start)
    label = {s: f"{START_YEAR[s]}/{str(START_YEAR[s] + 1)[2:]}" for s in START_YEAR}
    parts, cs = [], {}
    for test in TEST_SEASONS:
        train, te = g[g["season_id"] < test], g[g["season_id"] == test]
        res, c, m = fit_predict(train, te)
        res["stagione"] = label[test]
        parts.append(res)
        cs[label[test]] = c
        print(label[test], "c bayes =", c, flush=True)
    res = pd.concat(parts)
    imp = pd.Series(m.feature_importance("gain"), index=FEATS)
    out = ["# Minuti in stagione: aggiornamento dopo ogni partita", "",
           "Generato da `scripts/minuti_in_stagione.py`. Errore medio assoluto in minuti per la partita,",
           "per numero di partite gia' giocate dal giocatore nella stagione (k). Stagioni di test 2022/23-2025/26,",
           "ognuna prevista allenando solo sulle precedenti. Giocatori della rosa di inizio stagione, quando giocano.", "",
           "- `inizio` = stima di inizio stagione ferma (come in produzione oggi)",
           "- `ewm` = media esponenziale dei minuti della stagione", "- `bayes` = (c * inizio + somma minuti) / (c + k)",
           "- `lgbm` = LightGBM unico con minuti recenti, partenze, assenti di regime ecc.",
           "- `separati` = un modello per la prima partita (LightGBM piccolo o lineare: inizio, inizio^2, assenti,",
           "  partite della squadra) e un LightGBM per le partite successive, allenati su righe separate", "",
           "## Per numero di partite giocate", "", by_k(res).to_markdown(floatfmt=".2f"), "",
           "## Prima partita: medie per fascia della stima di inizio stagione (bias)", "",
           k0_bias(res).to_markdown(floatfmt=".2f"), "",
           "## Per stagione", "",
           pd.DataFrame({m: res.groupby("stagione").apply(lambda x: np.abs(x["min240"] - x[m]).mean(),
                                                           include_groups=False) for m in METHODS}).to_markdown(floatfmt=".2f"), "",
           f"c scelto per il bayes: {cs}", "",
           "## Importanza variabili (LightGBM partite successive, ultimo allenamento)", "",
           (imp / imp.sum()).sort_values(ascending=False).round(3).to_frame("quota").to_markdown(), ""]
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    res.to_parquet(ROOT / "data" / "minuti_in_stagione_validazione.parquet", index=False)


# --------------------------------------------------------------------------- produzione

def current_features(db, pg, since=SEASON_START):
    """Variabili "prima della prossima partita" per i giocatori di stima_minuti_avvio, dalle partite
    giocate da `since` e dalla lista infortuni attuale (probability_of_playing)."""
    st = pd.read_sql("""SELECT fantasy_id, player_id, name, minutes, minutes_all_healthy
                        FROM stima_minuti_avvio""", db)
    # squadra e probabilita' di giocare ATTUALI (la stima di avvio e' congelata a inizio stagione)
    now = pd.read_sql("""
        SELECT f.fantasy_id, f.team_id, l.probability_of_playing
        FROM fantasy_players f
        LEFT JOIN fantasy_listone l ON l.fantasy_id = f.fantasy_id
             AND l.matchday_id = (SELECT MAX(matchday_id) FROM fantasy_listone)""", db)
    st = st.merge(now, on="fantasy_id", how="left")
    st["start"] = st["minutes"].fillna(st["minutes_all_healthy"])
    cur = pg[pg["game_date"] >= since].sort_values(["game_date", "game_id"])
    g = cur.groupby("player_id")
    f = pd.DataFrame({
        "k": g.size(), "last1": g["min240"].last(),
        "last3": g["min240"].apply(lambda x: x.tail(3).mean()), "last5": g["min240"].apply(lambda x: x.tail(5).mean()),
        "ewm5": g["min240"].apply(lambda x: x.ewm(halflife=3).mean().iloc[-1]),
        "ewm15": g["min240"].apply(lambda x: x.ewm(halflife=10).mean().iloc[-1]),
        "season_mean": g["min240"].mean(), "starter5": g["starter"].apply(lambda x: x.tail(5).mean()),
        "cum_sum": g["min240"].sum()})
    d = st.merge(f, left_on="player_id", right_index=True, how="left")
    d["k"] = d["k"].fillna(0)
    d["cum_sum"] = d["cum_sum"].fillna(0)
    d["t"] = d["team_id"].map(cur.groupby("team_id")["game_id"].nunique()).fillna(0)
    d["played_frac"] = d["k"] / d["t"].where(d["t"] > 0)
    # compagni di regime assenti oggi (esclude il giocatore stesso), come nello storico: fuori nella lista
    # infortuni, oppure non hanno giocato nessuna delle ultime MIN_RUN partite della squadra
    last_games = (cur[["team_id", "game_id", "game_date"]].drop_duplicates().sort_values(["game_date", "game_id"])
                  .groupby("team_id").tail(MIN_RUN))
    seen = set(map(tuple, cur.merge(last_games[["team_id", "game_id"]])[["team_id", "player_id"]].to_numpy()))
    streak = (d["t"] >= MIN_RUN) & ~pd.Series([(tm, pid) in seen for tm, pid in zip(d["team_id"], d["player_id"])],
                                              index=d.index)
    out = ((d["probability_of_playing"].fillna(1) < MIN_PLAY_PROB) | streak) & (d["start"] >= ROTATION)
    d["_mm"], d["_mn"] = np.where(out, d["start"], 0.0), out.astype(int)
    d["miss_min"] = d.groupby("team_id")["_mm"].transform("sum") - d["_mm"]
    d["miss_n"] = d.groupby("team_id")["_mn"].transform("sum") - d["_mn"]
    return d.drop(columns=["_mm", "_mn"])


def predict_current():
    db = sqlite3.connect(DB, timeout=30)
    pg = games_with_home(db)
    hist = season_games(pg, pd.read_parquet(START_FILE))
    models = fit_models(hist)
    d = current_features(db, pg)
    # casa/trasferta: media delle due previsioni (la prossima partita puo' essere l'una o l'altra)
    pred = np.mean([predict_models(models, d.assign(home=h)) for h in (0, 1)], axis=0)
    d["minutes"] = np.clip(pred, 1, CAP)
    d["created_at"] = pd.Timestamp.now().isoformat(timespec="seconds")
    out = d[["fantasy_id", "player_id", "name", "team_id", "minutes", "start", "k", "t", "last1", "ewm5",
             "miss_min", "probability_of_playing", "created_at"]]
    out.round(2).to_sql("stima_minuti_stagione", db, if_exists="replace", index=False)
    db.commit()
    print(f"salvati {len(out)} giocatori in stima_minuti_stagione (partite giocate in stagione: max k = "
          f"{int(d['k'].max())})")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--prevedi", action="store_true", help="minuti per la prossima partita delle rose attuali")
    if ap.parse_args().prevedi:
        predict_current()
    else:
        main()
