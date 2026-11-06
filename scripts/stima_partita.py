"""
Stima del punteggio Dunkest di ogni giocatore delle rose attuali nelle prossime giornate Dunkest
(tutte le partite della giornata, sommate), con valore medio e range (quantili 10-30-50-70-90%), dato che il giocatore gioca.

Catena:
  minuti     stima_minuti_stagione (scripts/minuti_in_stagione.py --prevedi: stima di inizio stagione
             aggiornata con le partite giocate e gli assenti), altrimenti stima_minuti_avvio, + variabilita' partita per partita
             (rapporto minuti / minuti abituali osservato nello storico, per fascia di minuti)
  tassi      LightGBM per ogni statistica (tasso al minuto, offset log minuti) e percentuali di tiro,
             allenati su 2018/19-2025/26; variabilita' binomiale negativa (alpha stimato sui dati)
  partita    simulazione Monte Carlo: minuti, fattore "serata" comune, conteggi, realizzati,
             titolare, vittoria (uguale per tutti i giocatori della stessa squadra)
  punteggio  formula Dunkest (docs/punteggio.md) su ogni partita simulata

Uscite in data/dunkest.sqlite (le giornate calcolate vengono sostituite, le altre restano):
  stima_partita   una riga per giocatore e partita
  stima_giornata  una riga per giocatore e giornata (somma delle partite: media e range)
  stima_input     variabili di ogni giocatore e partita: la pagina rifa' la stima se l'utente cambia i minuti
                  (resimulate, con data/models/minutes_noise.npz)

    python stima_partita.py                 # prossime 3 giornate (modelli in data/models, allenati se mancano)
    python stima_partita.py --giornate 5    # prossime 5 giornate
    python stima_partita.py --riallena      # riallena i modelli
"""

import argparse
import json
import sqlite3

import lightgbm as lgb      # prima di pandas/pyarrow: su Windows altrimenti conflitto OpenMP (crash in predict)
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from stat_dati import (COUNTS, OPP_HALF_LIFE, OUT as STAT_DATASET, PCTS, bio_features, league_totals,
                       rate_features)
from stat_modelli import LGB_PARAMS, features, nb2_alpha
from forza_squadre import HALF_LIFE as RATING_HL, OUT as FORZA, RATE_STATS, current_ratings
from stima_dati import (DB, HALF_LIVES, OUT as BASE_DATASET, ROOT, ROTATION_MIN, dunkest_base, dunkest_bonuses,
                        dunkest_score, load, team_games)

MODELS = ROOT / "data" / "models"
VALID_SEASON = 25
N_SIM = 2000
N_MATCHDAYS = 3
FORM_VAR = 0.03            # varianza del fattore "serata" comune a tutte le statistiche
MIN_PLAY_PROB = 0.5
SEED = 7
NOISE_FILE = MODELS / "minutes_noise.npz"
# colonne di stima_input oltre alle variabili dei modelli
INPUT_COLS = ["fantasy_id", "player_id", "name", "team_id", "opp_id", "home", "game_date", "match_id", "game_key",
              "probability_of_playing", "rookie", "pred_min", "ms_starter", "pick"]


# --------------------------------------------------------------------------- modelli

def train_models(sd):
    """LightGBM finali: iterazioni scelte con early stopping (allenamento <=2024/25, validazione 2025/26),
    poi riallenati su tutto. Salvati in data/models con alpha NB2 per i conteggi."""
    MODELS.mkdir(parents=True, exist_ok=True)
    tr, va = sd[sd["season_id"] < VALID_SEASON], sd[sd["season_id"] == VALID_SEASON]
    meta = {}

    def fit(name, obj, f, d_tr, d_va, y, w=None, init=None):
        p = dict(LGB_PARAMS, objective=obj)
        ds = lgb.Dataset(d_tr[f], y(d_tr), weight=w(d_tr) if w else None, init_score=init(d_tr) if init else None)
        dv = lgb.Dataset(d_va[f], y(d_va), weight=w(d_va) if w else None,
                         init_score=init(d_va) if init else None, reference=ds)
        it = lgb.train(p, ds, 2000, valid_sets=[dv], callbacks=[lgb.early_stopping(50, verbose=False)]).best_iteration
        full = pd.concat([d_tr, d_va])
        m = lgb.train(p, lgb.Dataset(full[f], y(full), weight=w(full) if w else None,
                                     init_score=init(full) if init else None), it)
        m.save_model(str(MODELS / f"{name}.txt"))
        return m, full, it

    for c in COUNTS:
        f = features(c)
        m, full, it = fit(c, "poisson", f, tr, va, lambda x: x[c], init=lambda x: np.log(x["minutes"]))
        mu = np.exp(m.predict(full[f], raw_score=True)) * full["minutes"]
        meta[c] = {"iterations": it, "alpha": nb2_alpha(full[c].values, mu.values), "features": f}
        print(f"modello {c}: {it} iterazioni, alpha {meta[c]['alpha']:.3f}", flush=True)
    for k, (made, att, _) in PCTS.items():
        f = features()
        m, _, it = fit(k, "cross_entropy", f, tr[tr[att] > 0], va[va[att] > 0], lambda x: x[made] / x[att],
                       w=lambda x: x[att])
        meta[k] = {"iterations": it, "features": f}
        print(f"modello {k}: {it} iterazioni", flush=True)
    (MODELS / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")


def load_models():
    meta = json.loads((MODELS / "meta.json").read_text(encoding="utf-8"))
    return {k: lgb.Booster(model_file=str(MODELS / f"{k}.txt")) for k in meta}, meta


# --------------------------------------------------------------------------- stato attuale

def ewm_last(df, keys, cols, hl):
    """Media esponenziale su TUTTE le partite (nessuno shift): lo stato prima della prossima partita."""
    return df.groupby(keys)[cols].apply(lambda g: g.ewm(halflife=hl, ignore_na=True).mean().iloc[-1])


def player_state(pg):
    pg = pg.sort_values(["player_id", "game_date", "game_id"]).copy()
    pg["fg2a"], pg["fg2m"] = pg["fga"] - pg["tpa"], pg["fgm"] - pg["tpm"]
    cols = sorted(set(COUNTS + ["fg2m", "tpm", "ftm", "fga", "ast"])) + ["minutes", "starter"]
    st = pg.groupby("player_id").agg(career_games=("game_id", "size"), last_team=("team_id", "last"),
                                     stats_position=("position", "last"))
    for tag, hl in HALF_LIVES.items():
        ew = ewm_last(pg, "player_id", cols, hl)
        for c in cols[:-2]:
            st[f"r{tag}_{c}"] = ew[c] / ew["minutes"]
        st[f"m{tag}_min"], st[f"m{tag}_starter"] = ew["minutes"], ew["starter"]
    return st.reset_index()


def rating_state(db, pg):
    """Tassi al minuto per la forza di squadra (stesse definizioni di forza_squadre.py), ultimo valore."""
    pm = pd.read_sql("SELECT game_id, player_id, plus_minus AS pm FROM player_games", db)
    pg = pg.merge(pm, on=["game_id", "player_id"]).sort_values(["player_id", "game_date", "game_id"])
    ew = ewm_last(pg, "player_id", RATE_STATS + ["minutes"], RATING_HL)
    out = pd.DataFrame({f"r_{c}": ew[c] / ew["minutes"] for c in RATE_STATS}, index=ew.index)
    return out.reset_index()


def opponent_state(pg):
    pg = pg.copy()
    pg["fg2a"] = pg["fga"] - pg["tpa"]
    g = (pg.groupby(["game_id", "game_date", "opponent_team_id", "position"])[COUNTS + ["minutes"]]
         .sum().reset_index().sort_values(["game_date", "game_id"]))
    ew = ewm_last(g, ["opponent_team_id", "position"], COUNTS + ["minutes"], OPP_HALF_LIFE)
    ew = ew.rename(columns={c: f"oa_{c}" for c in COUNTS} | {"minutes": "oa_minutes"}).reset_index()
    return ew.rename(columns={"opponent_team_id": "opp_id"})


def team_state(pg):
    tg = team_games(pg)
    return ewm_last(tg, "team_id", ["pace", "margin"], 15).reset_index()


def upcoming_matchdays(db, n):
    """Le prossime n giornate a partire da quella del listone attuale."""
    cur = pd.read_sql("SELECT MAX(matchday_id) m FROM fantasy_listone", db)["m"].iloc[0]
    return pd.read_sql("""SELECT matchday_id, number FROM fantasy_matchdays WHERE matchday_id >= ?
                          ORDER BY number LIMIT ?""", db, params=(int(cur), n))


def matchday_games(db, matchday_id):
    """Per ogni squadra la PRIMA partita della giornata (regola Dunkest: conta solo la prima partita giocata,
    anche se la squadra ne gioca 2-3 nella stessa giornata)."""
    m = pd.read_sql("""SELECT match_id, started_at, home_team_id, away_team_id FROM fantasy_matches
                       WHERE matchday_id = ?""", db, params=(int(matchday_id),))
    rows = [{"match_id": r["match_id"], "team_id": r[t], "opp_id": r[o], "home": int(t == "home_team_id"),
             "started_at": r["started_at"]}
            for _, r in m.iterrows() for t, o in (("home_team_id", "away_team_id"), ("away_team_id", "home_team_id"))]
    g = pd.DataFrame(rows)
    # data della partita negli Stati Uniti (gli orari sono UTC: le partite serali finiscono nel giorno dopo)
    g["game_date"] = (pd.to_datetime(g["started_at"]).dt.tz_localize(None) - pd.Timedelta(hours=6)).dt.normalize()
    g = g.sort_values(["team_id", "started_at"]).drop_duplicates("team_id", keep="first")
    g["b2b"] = (g.groupby("team_id")["game_date"].diff().dt.days <= 1).astype(int)
    g["game_key"] = g["match_id"].astype(str)
    return g


def roster(db):
    return pd.read_sql("""
        SELECT s.fantasy_id, s.player_id, s.name, s.team_id,
               COALESCE(ms.minutes, s.minutes) AS pred_min,
               COALESCE(ms.minutes, s.minutes_all_healthy) AS minutes_all_healthy,
               s.probability_of_playing, s.rookie,
               CASE f.position WHEN 'Guard' THEN 'G' WHEN 'Forward' THEN 'F' ELSE 'C' END AS fantasy_position,
               b.height_cm, b.weight_kg, b.birth_date, d.pick
        FROM stima_minuti_avvio s JOIN fantasy_players f USING (fantasy_id)
        LEFT JOIN stima_minuti_stagione ms ON ms.fantasy_id = s.fantasy_id
        LEFT JOIN players_bio pb ON pb.fantasy_id = s.fantasy_id OR (pb.player_id = s.player_id AND pb.player_id IS NOT NULL)
        LEFT JOIN bref_players b ON b.bref_id = pb.bref_id
        LEFT JOIN bref_draft d ON d.bref_id = pb.bref_id
        GROUP BY s.fantasy_id""", db)


def current_states(db, pg):
    """Stato attuale di giocatori, avversari e squadre: si calcola una volta per tutte le giornate."""
    return {"player": player_state(pg), "opp": opponent_state(pg), "team": team_state(pg),
            "rating": rating_state(db, pg)}


def build_features(db, states, league, games):
    """Una riga per giocatore e partita della giornata (giocatori di squadre a riposo esclusi)."""
    r = roster(db)
    r = r.merge(states["player"], on="player_id", how="left")
    r["position"] = r["stats_position"].fillna(r["fantasy_position"])
    r["career_games"] = r["career_games"].fillna(0)
    r = r.merge(games, on="team_id", how="inner")
    r = r.merge(states["opp"], on=["opp_id", "position"], how="left")
    ts = states["team"]
    r = r.merge(ts.rename(columns={"pace": "t_pace", "margin": "t_margin"}), on="team_id", how="left")
    r = r.merge(ts.rename(columns={"team_id": "opp_id", "pace": "o_pace", "margin": "o_margin"}), on="opp_id", how="left")

    # compagni assenti oggi (lista infortuni Dunkest), come nello storico: solo chi giocava >= ROTATION_MIN
    r["available"] = r["probability_of_playing"].fillna(1) >= MIN_PLAY_PROB
    r["pred_min"] = r["pred_min"].fillna(r["minutes_all_healthy"])
    one = r.drop_duplicates("fantasy_id")          # una riga per giocatore (una squadra puo' giocare piu' partite)
    out = one[~one["available"] & (one["ml_min"] >= ROTATION_MIN)].assign(
        usage=lambda x: (x["rl_fga"] + x["rl_ast"]) * x["ml_min"])
    miss = out.groupby("team_id").agg(miss_usage=("usage", "sum")).reset_index()
    same = out.groupby(["team_id", "position"]).agg(miss_same=("ml_min", "sum")).reset_index()
    r = r.merge(miss, on="team_id", how="left").merge(same, on=["team_id", "position"], how="left")

    # forza delle squadre dalle rose: giocatori disponibili pesati per i minuti previsti
    r = r.merge(states["rating"], on="player_id", how="left")
    ratings = current_ratings(r[r["available"]].drop_duplicates("fantasy_id"), min_col="pred_min")
    r = r.merge(ratings.rename(columns={"rating": "t_rating"}), on="team_id", how="left")
    r = r.merge(ratings.rename(columns={"team_id": "opp_id", "rating": "o_rating"}), on="opp_id", how="left")

    r = rate_features(r, league)
    r = bio_features(r, r["game_date"])
    r["dev"] = np.log(r["pred_min"] / r["ms_min"].fillna(r["pred_min"])).clip(-2.5, 1.5)
    r["pace"] = np.log((r["t_pace"].fillna(100) + r["o_pace"].fillna(100)) / 200)
    r["season_games"], r["missed_before"] = 0, 0
    r["miss_usage10"] = r["miss_usage"].fillna(0) / 10
    r["miss_same30"] = r["miss_same"].fillna(0) / 30
    r["new_team"] = ((r["last_team"] != r["team_id"]) & r["last_team"].notna()).astype(int)
    r["pos_code"] = r["position"].map({"G": 0, "F": 1, "C": 2})
    return r


# --------------------------------------------------------------------------- simulazione

def minutes_noise(base):
    """Rapporti minuti reali / minuti abituali (media recente) per fascia di minuti abituali."""
    b = base[base["ms_min"] >= 3][["ms_min", "minutes"]]
    edges = np.quantile(b["ms_min"], np.linspace(0, 1, 11)[1:-1])
    bins = np.digitize(b["ms_min"], edges)
    ratios = [(b["minutes"] / b["ms_min"]).values[bins == i] for i in range(10)]
    return edges, ratios


def save_noise(noise):
    edges, ratios = noise
    np.savez(NOISE_FILE, edges=edges, **{f"r{i}": r for i, r in enumerate(ratios)})


def load_noise():
    z = np.load(NOISE_FILE)
    return z["edges"], [z[f"r{i}"] for i in range(len(z["edges"]) + 1)]


def win_model(base):
    """Probabilita' di vittoria dalla differenza di forza (rose) e dal fattore campo."""
    d = (base[["game_id", "team_id", "home", "win"]].drop_duplicates(["game_id", "team_id"])
         .merge(pd.read_parquet(FORZA), on=["game_id", "team_id"]))
    d["diff"] = d["t_rating"] - d["o_rating"]
    return smf.logit("win ~ diff + home", data=d).fit(disp=0)


def simulate(r, models, meta, noise, wmod, rng):
    """wmod=None: probabilita' di vittoria gia' nella colonna p_win (ricalcolo di una singola partita)."""
    n, S = len(r), N_SIM
    edges, ratios = noise
    mins = np.empty((n, S))
    bins = np.digitize(r["pred_min"].values, edges)
    for b in np.unique(bins):
        idx = bins == b
        mins[idx] = r["pred_min"].values[idx, None] * rng.choice(ratios[b], size=(idx.sum(), S))
    mins = np.clip(mins, 1, 48)
    form = rng.gamma(1 / FORM_VAR, FORM_VAR, size=(n, S))

    c_sim = {}
    for c in COUNTS:
        rate = np.exp(models[c].predict(np.ascontiguousarray(r[meta[c]["features"]].astype(float).values), raw_score=True))
        lam = rate[:, None] * mins * form
        a = meta[c]["alpha"]
        if a > 0:
            lam = lam * rng.gamma(1 / a, a, size=(n, S))
        c_sim[c] = rng.poisson(lam)
        r[f"rate_{c}"] = rate
    p = {k: models[k].predict(np.ascontiguousarray(r[meta[k]["features"]].astype(float).values)) for k in PCTS}
    fg2m = rng.binomial(c_sim["fg2a"], p["fg2"][:, None])
    tpm = rng.binomial(c_sim["tpa"], p["tp"][:, None])
    ftm = rng.binomial(c_sim["fta"], p["ft"][:, None])

    # titolare: quota di partenze da titolare recente; rookie per fascia di draft
    pick = r["pick"].fillna(99)
    rookie_st = pd.Series(np.select([pick <= 5, pick <= 14], [0.6, 0.25], 0.05), index=r.index)
    p_st = r["ms_starter"].fillna(rookie_st).values
    starter = (rng.random((n, S)) < p_st[:, None]).astype(float)

    # vittoria: probabilita' della squadra di casa, UNA estrazione per partita condivisa da tutti i giocatori
    # (chi gioca in casa vince se u < p, chi gioca fuori se u >= p)
    home = r["home"].fillna(0).values
    if wmod is None:
        p_win = r["p_win"].values.astype(float)
        p_home = np.where(home == 1, p_win, 1 - p_win)
    else:
        diff_home = np.where(home == 1, 1, -1) * (r["t_rating"].fillna(0) - r["o_rating"].fillna(0)).values
        p_home = wmod.predict(pd.DataFrame({"diff": diff_home, "home": np.ones(n)})).values
        p_win = np.where(home == 1, p_home, 1 - p_home)
    u = {g: rng.random(S) for g in r["game_key"].dropna().unique()}
    win = np.zeros((n, S))
    for i, g in enumerate(r["game_key"].values):
        if isinstance(g, str):
            win[i] = (u[g] < p_home[i]) if home[i] == 1 else (u[g] >= p_home[i])

    box = dict(pts=2 * fg2m + 3 * tpm + ftm, oreb=c_sim["oreb"], dreb=c_sim["dreb"], ast=c_sim["ast"],
               stl=c_sim["stl"], blk=c_sim["blk"], blka=c_sim["blka"], tov=c_sim["tov"], fgm=fg2m + tpm,
               fga=c_sim["fg2a"] + c_sim["tpa"], ftm=ftm, fta=c_sim["fta"], tpm=tpm, starter=starter)
    fp = dunkest_score(box, win)
    out = r[["fantasy_id", "player_id", "name", "team_id", "opp_id", "home", "game_date", "match_id",
             "probability_of_playing", "rookie"]].copy()
    out["minutes"] = mins.mean(axis=1)
    # box score medio (starter = probabilita' di partire titolare) e bonus medi: per linearita' della media
    # fp_mean = somma voce x peso della formula + bonus, cosi' la pagina mostra da dove viene il punteggio
    for k in ["pts", "oreb", "dreb", "ast", "stl", "blk", "blka", "tov", "fgm", "fga", "ftm", "fta", "tpm", "starter"]:
        out[k] = box[k].mean(axis=1)
    out["tpa"] = c_sim["tpa"].mean(axis=1)
    b_doubles, b_threes = dunkest_bonuses(box)
    out["bonus_doppie"], out["bonus_triple"] = b_doubles.mean(axis=1), b_threes.mean(axis=1)
    out["bonus_vittoria"] = (fp - dunkest_base(box)).mean(axis=1)
    out["p_win"] = p_win
    out["fp_mean"] = fp.mean(axis=1)
    # "partita tipo": la simulazione piu' vicina alla stima, nel punteggio (peso 3), nelle statistiche e nei minuti
    # (distanza standardizzata). E' un box score vero: interi, bonus pieni, titolare e vittoria si'/no;
    # la pagina lo mostra e l'utente lo modifica
    rep_stats = dict(pts=box["pts"], dreb=box["dreb"], oreb=box["oreb"], ast=box["ast"], stl=box["stl"], blk=box["blk"],
                     blka=box["blka"], tov=box["tov"], miss_fg=box["fga"] - box["fgm"], miss_ft=box["fta"] - box["ftm"],
                     tpm=box["tpm"])
    z = lambda x: (x - x.mean(axis=1, keepdims=True)) / (x.std(axis=1, keepdims=True) + 0.5)
    dist = (sum(z(x) ** 2 for x in rep_stats.values()) + z(mins) ** 2) / (len(rep_stats) + 1) + 3 * z(fp) ** 2
    pick_s = dist.argmin(axis=1)
    rows_i = np.arange(n)
    out["rep_minutes"] = np.round(mins[rows_i, pick_s])
    for k, x in rep_stats.items():
        out[f"rep_{k}"] = x[rows_i, pick_s]
    out["rep_starter"], out["rep_win"] = starter[rows_i, pick_s], win[rows_i, pick_s]
    out["rep_fp"] = fp[rows_i, pick_s]
    out["fp_q10"], out["fp_q30"], out["fp_q50"], out["fp_q70"], out["fp_q90"] = np.quantile(
        fp, [0.1, 0.3, 0.5, 0.7, 0.9], axis=1)
    return out, fp


def model_features(meta):
    return sorted(set().union(*(m["features"] for m in meta.values())))


def resimulate(row, minutes, models, meta, noise, seed=SEED):
    """Stima di UNA partita con minuti scelti dall'utente: stessi modelli e stessa simulazione,
    cambiano i minuti previsti e quindi la variabile dev (minuti previsti / abituali) dei tassi al minuto."""
    r = pd.DataFrame([row])
    feats = model_features(meta)
    for c in feats + ["pred_min", "ms_starter", "pick", "home", "p_win"]:
        r[c] = pd.to_numeric(r[c], errors="coerce")
    r["pred_min"] = float(minutes)
    r["dev"] = np.log(r["pred_min"] / r["ms_min"].fillna(r["pred_min"])).clip(-2.5, 1.5)
    out, _ = simulate(r, models, meta, noise, None, np.random.default_rng(seed))
    return {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in out.iloc[0].to_dict().items()}


def per_matchday(out, fp):
    """Somma delle partite della giornata per giocatore (campione per campione: range corretto)."""
    rows = []
    for fid, idx in out.groupby("fantasy_id").indices.items():
        tot = fp[idx].sum(axis=0)
        g = out.iloc[idx]
        rows.append({"fantasy_id": fid, "n_games": len(idx), "minutes": g["minutes"].sum(),
                     "fp_mean": tot.mean(), "fp_q10": np.quantile(tot, 0.1), "fp_q30": np.quantile(tot, 0.3),
                     "fp_q50": np.quantile(tot, 0.5), "fp_q70": np.quantile(tot, 0.7), "fp_q90": np.quantile(tot, 0.9),
                     "probability_of_playing": g["probability_of_playing"].iloc[0]})
    return pd.DataFrame(rows)


def save(db, table, df, matchday_ids):
    """Sostituisce solo le giornate calcolate."""
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if exists:
        cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if set(df.columns) - cols:            # schema cambiato: ricrea la tabella
            db.execute(f"DROP TABLE {table}")
        else:
            db.execute(f"DELETE FROM {table} WHERE matchday_id IN ({','.join('?' * len(matchday_ids))})",
                       [int(m) for m in matchday_ids])
    df.round(2).to_sql(table, db, if_exists="append", index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--riallena", action="store_true")
    ap.add_argument("--giornate", type=int, default=N_MATCHDAYS)
    args = ap.parse_args()
    sd = pd.read_parquet(STAT_DATASET)
    sd["pos_code"] = sd["position"].map({"G": 0, "F": 1, "C": 2})
    if args.riallena or not (MODELS / "meta.json").exists():
        train_models(sd)
    models, meta = load_models()

    db = sqlite3.connect(DB, timeout=30)
    pg = load(db)
    base = pd.read_parquet(BASE_DATASET)
    states, league = current_states(db, pg), league_totals(sd)
    noise, wmod, rng = minutes_noise(base), win_model(base), np.random.default_rng(SEED)
    save_noise(noise)
    feats = model_features(meta)
    inputs_all = []
    now = pd.Timestamp.now().isoformat(timespec="seconds")
    mds = upcoming_matchdays(db, args.giornate)
    games_all, days_all = [], []
    for md, number in mds.itertuples(index=False):
        r = build_features(db, states, league, matchday_games(db, md))
        out, fp = simulate(r, models, meta, noise, wmod, rng)
        day = per_matchday(out, fp)
        inp = r[INPUT_COLS + [c for c in feats if c not in INPUT_COLS]].copy()
        inp["p_win"] = out["p_win"]
        inp["game_date"] = inp["game_date"].dt.strftime("%Y-%m-%d")
        inp.insert(0, "matchday_id", md)
        inputs_all.append(inp)
        for df in (out, day):
            df.insert(0, "matchday_id", md)
            df["model"], df["created_at"] = "lgbm", now
        out["game_date"] = out["game_date"].dt.strftime("%Y-%m-%d")
        games_all.append(out)
        days_all.append(day)
        print(f"giornata {number}: {out['match_id'].nunique()} partite, {len(day)} giocatori", flush=True)
    save(db, "stima_partita", pd.concat(games_all), mds["matchday_id"])
    save(db, "stima_giornata", pd.concat(days_all), mds["matchday_id"])
    save(db, "stima_input", pd.concat(inputs_all), mds["matchday_id"])
    db.commit()
    print("salvato in stima_partita e stima_giornata")


if __name__ == "__main__":
    main()
