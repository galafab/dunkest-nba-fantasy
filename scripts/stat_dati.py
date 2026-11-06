"""
Dataset per i modelli delle singole statistiche (tassi al minuto e percentuali di tiro).

Parte da data/stima_dataset.parquet (stima_dati.py) e aggiunge, sempre usando solo dati
precedenti alla partita:
  - oa_<stat>: quanto l'avversario concede di quella statistica al minuto al ruolo del giocatore
               (media esponenziale sulle sue partite precedenti);
  - tassi del giocatore "ristretti" verso la media del ruolo quando ha pochi minuti di storico
    (lungo periodo e forma recente), percentuali di tiro ristrette verso la media del ruolo;
  - dev = log(minuti / minuti abituali): elasticita' delle statistiche ai minuti;
  - anagrafica: eta', altezza, peso (Basketball Reference);
  - t_rating / o_rating: forza della squadra e dell'avversario dalle rose (forza_squadre.py).

Uscita: data/stat_dataset.parquet (letto anche dallo script R dei GLMM)

    python stat_dati.py
"""

import sqlite3

import numpy as np
import pandas as pd

from stima_dati import DB, OUT as BASE_DATASET, ROOT

OUT = ROOT / "data" / "stat_dataset.parquet"
FORZA = ROOT / "data" / "forza_squadre.parquet"
COUNTS = ["fg2a", "tpa", "fta", "oreb", "dreb", "ast", "stl", "blk", "blka", "tov"]
PCTS = {"fg2": ("fg2m", "fg2a", 60), "tp": ("tpm", "tpa", 60), "ft": ("ftm", "fta", 30)}  # n0 tentativi
TRAIN_MAX_SEASON = 19            # medie di lega calcolate solo fino al 2024/25
K0_MIN = 300                     # forza della media di ruolo nella restrizione (minuti)
WINDOW = {"l": 25, "s": 5}       # partite "efficaci" delle medie esponenziali
OPP_HALF_LIFE = 15
K0_OPP = 30                      # minuti "virtuali" di media di lega nel tasso concesso dall'avversario


def opponent_allowance(d):
    """Per ogni partita: tasso al minuto concesso dall'avversario a quel ruolo, prima della partita."""
    g = (d.groupby(["game_id", "game_date", "opponent_team_id", "position"])[COUNTS + ["minutes"]]
         .sum().reset_index().sort_values(["game_date", "game_id"]))
    keys = ["opponent_team_id", "position"]
    shifted = g.groupby(keys)[COUNTS + ["minutes"]].shift()
    ew = (shifted.groupby([g[k] for k in keys]).ewm(halflife=OPP_HALF_LIFE, ignore_na=True).mean()
          .reset_index(level=[0, 1], drop=True).sort_index())
    # medie per partita (numeratore e minuti): il rapporto si fa dopo, con la restrizione verso la lega
    for c in COUNTS:
        g[f"oa_{c}"] = ew[c]
    g["oa_minutes"] = ew["minutes"]
    return g[["game_id", "opponent_team_id", "position", "oa_minutes"] + [f"oa_{c}" for c in COUNTS]]


def bio(db):
    return pd.read_sql("""
        SELECT pb.player_id, b.pos AS bref_pos, b.height_cm, b.weight_kg, b.birth_date
        FROM players_bio pb JOIN bref_players b USING (bref_id)
        WHERE pb.player_id IS NOT NULL""", db).drop_duplicates("player_id")


def main():
    d = pd.read_parquet(BASE_DATASET)
    d["fg2m"], d["fg2a"] = d["fgm"] - d["tpm"], d["fga"] - d["tpa"]
    for t in WINDOW:
        d[f"r{t}_fg2a"] = d[f"r{t}_fga"] - d[f"r{t}_tpa"]
        d[f"r{t}_fg2m"] = d[f"r{t}_fgm"] - d[f"r{t}_tpm"]

    d = d.merge(opponent_allowance(d), on=["game_id", "opponent_team_id", "position"], how="left")
    # forza delle due squadre dalle rose disponibili (scripts/forza_squadre.py)
    d = d.merge(pd.read_parquet(FORZA, columns=["game_id", "team_id", "t_rating", "o_rating"]),
                on=["game_id", "team_id"], how="left")
    league = league_totals(d)
    d = rate_features(d, league)

    usual = d["ms_min"].fillna(d["ml_min"])
    d["dev"] = np.log(d["minutes"] / usual).clip(-2.5, 1.5)
    d["pace"] = np.log((d["t_pace"].fillna(100) + d["o_pace"].fillna(100)) / 200)
    d["b2b"] = (d["t_rest"] <= 1).astype(int)

    db = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    d = bio_features(d.merge(bio(db), on="player_id", how="left"), d["game_date"])
    d["miss_usage10"] = d["miss_usage"] / 10
    d["miss_same30"] = d["miss_min_same_pos"] / 30
    d["season_key"] = d["player_id"].astype(str) + "_" + d["season_id"].astype(str)

    d = d[d["career_games"] >= 1]
    keep = (["game_id", "player_id", "season_id", "season_key", "game_date", "team_id", "opponent_team_id",
             "position", "bref_pos", "home", "win", "minutes", "starter", "career_games", "season_games",
             "ms_min", "ml_min", "dev", "pace", "b2b", "new_team", "missed_before", "miss_usage10", "miss_same30",
             "t_rating", "o_rating",
             "z_age", "z_height_cm", "z_weight_kg", "fg2m", "tpm", "ftm"] + COUNTS
            + [f"{p}_{c}" for c in COUNTS for p in ("lr", "form", "loa", "rl", "rs")]
            + [f"lgt_{k}" for k in PCTS])
    d[keep].to_parquet(OUT, index=False)
    print(f"salvato {OUT}: {len(d):,} righe, {len(keep)} colonne")


def league_totals(d):
    """Totali di lega per ruolo (solo stagioni di allenamento): base della restrizione verso la media."""
    return d[d["season_id"] <= TRAIN_MAX_SEASON].groupby("position")[
        COUNTS + ["fg2m", "tpm", "ftm", "minutes"]].sum()


def bio_features(d, when):
    d["age"] = (pd.to_datetime(when).values - pd.to_datetime(d["birth_date"])).dt.days / 365.25
    for c, mu, sd in [("age", 27, 4), ("height_cm", 200, 9), ("weight_kg", 98, 11)]:
        d[f"z_{c}"] = ((d[c] - mu) / sd).fillna(0)
    return d


def rate_features(d, league):
    """Tassi ristretti (lr_, form_), avversario (loa_), percentuali (lgt_) da medie esponenziali grezze:
    r{l,s}_<stat> per minuto, m{l,s}_min, career_games, position, oa_<stat>/oa_minutes."""
    games_eff = d["career_games"]
    for c in COUNTS:
        lr = d["position"].map(league[c] / league["minutes"])
        for t, w in WINDOW.items():
            mins = d[f"m{t}_min"].fillna(0) * np.minimum(games_eff, w)
            d[f"sr{t}_{c}"] = (d[f"r{t}_{c}"].fillna(0) * mins + lr * K0_MIN) / (mins + K0_MIN)
        d[f"lr_{c}"] = np.log(d[f"srl_{c}"])                         # livello (log)
        d[f"form_{c}"] = np.log(d[f"srs_{c}"] / d[f"srl_{c}"])         # forma recente vs livello
        oa = (d[f"oa_{c}"].fillna(0) + lr * K0_OPP) / (d["oa_minutes"].fillna(0) + K0_OPP)
        d[f"loa_{c}"] = np.log(oa / lr)                                # avversario vs media di ruolo
    for k, (m, a, n0) in PCTS.items():
        lp = d["position"].map(league[m] / league[a])
        att = d[f"rl_{a}"].fillna(0) * d["ml_min"].fillna(0) * np.minimum(games_eff, WINDOW["l"])
        made = d[f"rl_{m}"].fillna(0) * d["ml_min"].fillna(0) * np.minimum(games_eff, WINDOW["l"])
        p = (made + n0 * lp) / (att + n0)
        d[f"lgt_{k}"] = np.log(p / (1 - p))
    return d


if __name__ == "__main__":
    main()
