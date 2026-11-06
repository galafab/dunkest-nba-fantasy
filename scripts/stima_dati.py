"""
Costruisce il dataset per la stima dei punteggi: una riga per giocatore e partita
giocata, con caratteristiche calcolate SOLO dalle partite precedenti (niente
informazioni dal futuro) e come obiettivo le statistiche del box score.

Il punteggio Dunkest e' sempre ricalcolato dai box score con la formula attuale
(docs/punteggio.md): i PDK delle stagioni passate usavano regole diverse.

Uscita: data/stima_dataset.parquet

    python stima_dati.py
"""

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "dunkest.sqlite"
OUT = ROOT / "data" / "stima_dataset.parquet"

# Statistiche da prevedere (conteggi). reb = oreb + dreb.
STATS = ["pts", "oreb", "dreb", "ast", "stl", "blk", "blka", "tov",
         "fgm", "fga", "ftm", "fta", "tpm", "tpa"]
HALF_LIVES = {"s": 5, "l": 25}          # medie esponenziali: forma recente / lungo periodo (partite)
ROTATION_MIN = 12                       # minuti medi oltre i quali un compagno assente "pesa"


# --------------------------------------------------------------------------- punteggio

def dunkest_bonuses(d):
    """Bonus doppie (doppia/tripla/quadrupla) e bonus triple segnate."""
    reb = d["oreb"] + d["dreb"]
    doubles = ((d["pts"] >= 10) * 1 + (reb >= 10) + (d["ast"] >= 10)
               + (d["stl"] >= 10) + (d["blk"] >= 10))
    return (np.select([doubles >= 4, doubles == 3, doubles == 2], [50, 10, 5], 0),
            np.select([d["tpm"] >= 5, d["tpm"] == 4, d["tpm"] == 3], [5, 4, 3], 0))


def dunkest_base(d):
    """Punteggio senza bonus vittoria. d: DataFrame (o dict di array) con i box score."""
    base = (d["pts"] + d["dreb"] + 1.25 * d["oreb"] + 1.5 * d["ast"] + 1.5 * d["stl"]
            - 1.5 * d["tov"] + 1.5 * d["blk"] - 0.5 * d["blka"]
            - (d["fga"] - d["fgm"]) - (d["fta"] - d["ftm"]) + d["starter"])
    b_doubles, b_threes = dunkest_bonuses(d)
    return base + b_doubles + b_threes


def dunkest_score(d, win):
    base = dunkest_base(d)
    return base + win * 0.05 * np.abs(base)


# --------------------------------------------------------------------------- caricamento

def load(db):
    pg = pd.read_sql("""
        SELECT g.game_id, g.player_id, g.season_id, g.game_date, g.team_id,
               g.opponent_team_id, g.opponent_side, g.win, g.minutes, g.starter, g.pdk,
               g.pts, g.oreb, g.dreb, g.ast, g.stl, g.blk, g.blka, g.tov,
               g.fgm, g.fga, g.ftm, g.fta, g.tpm, g.tpa, g.pf,
               p.position
        FROM player_games g JOIN players p USING (player_id)
        WHERE g.minutes > 0""", db)
    pg["game_date"] = pd.to_datetime(pg["game_date"])
    pg["home"] = (pg["opponent_side"] == "visitor").astype(int)
    pg["fp"] = dunkest_score(pg, pg["win"])
    pg["fp_base"] = dunkest_base(pg)
    return pg.sort_values(["game_date", "game_id", "player_id"]).reset_index(drop=True)


# --------------------------------------------------------------------------- squadre

def team_games(pg):
    """Una riga per squadra e partita: punti, possessi, margine, punti Dunkest concessi per ruolo."""
    tg = pg.groupby(["game_id", "team_id"]).agg(
        season_id=("season_id", "first"), game_date=("game_date", "first"),
        opp=("opponent_team_id", "first"), home=("home", "first"), win=("win", "max"),
        pts=("pts", "sum"), fga=("fga", "sum"), fta=("fta", "sum"),
        oreb=("oreb", "sum"), tov=("tov", "sum"), minutes=("minutes", "sum"),
    ).reset_index()
    tg["poss"] = tg["fga"] + 0.44 * tg["fta"] - tg["oreb"] + tg["tov"]
    opp = tg[["game_id", "team_id", "pts", "poss"]].rename(
        columns={"team_id": "opp", "pts": "opp_pts", "poss": "opp_poss"})
    tg = tg.merge(opp, on=["game_id", "opp"], how="left")
    tg["pace"] = 48 * (tg["poss"] + tg["opp_poss"]) / 2 / (tg["minutes"] / 5)
    tg["margin"] = tg["pts"] - tg["opp_pts"]

    # punti Dunkest (senza bonus vittoria) segnati CONTRO questa squadra, per ruolo, per 48'
    allowed = (pg.groupby(["game_id", "opponent_team_id", "position"])
               .agg(fp=("fp_base", "sum"), mins=("minutes", "sum")).reset_index())
    allowed["fp48"] = 48 * allowed["fp"] / allowed["mins"]
    allowed = allowed.pivot_table(index=["game_id", "opponent_team_id"], columns="position",
                                  values="fp48").add_prefix("allow_").reset_index()
    tg = tg.merge(allowed.rename(columns={"opponent_team_id": "team_id"}),
                  on=["game_id", "team_id"], how="left")

    # medie esponenziali PRIMA della partita (shift), continue tra stagioni
    tg = tg.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    cols = ["pace", "margin", "allow_G", "allow_F", "allow_C"]
    shifted = tg.groupby("team_id")[cols].shift()
    ew = (shifted.groupby(tg["team_id"]).ewm(halflife=15, ignore_na=True).mean()
          .reset_index(level=0, drop=True).sort_index())
    for c in cols:
        tg[f"t_{c}"] = ew[c]
    # riposo: giorni dall'ultima partita della squadra
    tg["t_rest"] = tg.groupby("team_id")["game_date"].diff().dt.days
    tg["t_games_7d"] = (tg.groupby("team_id", group_keys=False)
                        .apply(lambda g: g.rolling("7D", on="game_date")["game_id"].count() - 1,
                               include_groups=False))
    return tg


# --------------------------------------------------------------------------- giocatori

def player_features(pg):
    pg = pg.sort_values(["player_id", "game_date", "game_id"]).reset_index(drop=True)
    grp = pg.groupby("player_id")

    # medie esponenziali per minuto: ewm(stat) / ewm(minuti), entrambe prima della partita
    base_cols = STATS + ["minutes", "starter", "fp_base"]
    shifted = grp[base_cols].shift()
    for tag, hl in HALF_LIVES.items():
        ew = (shifted.groupby(pg["player_id"]).ewm(halflife=hl, ignore_na=True).mean()
              .reset_index(level=0, drop=True).sort_index())
        for c in STATS + ["fp_base"]:
            pg[f"r{tag}_{c}"] = ew[c] / ew["minutes"]          # per minuto
        pg[f"m{tag}_min"] = ew["minutes"]
        pg[f"m{tag}_starter"] = ew["starter"]
        pg[f"m{tag}_fp"] = ew["fp_base"]
    pg["last_min"] = shifted["minutes"]
    pg["last_starter"] = shifted["starter"]
    pg["last_fp"] = shifted["fp_base"]
    # deviazione standard recente del punteggio (variabilita' del giocatore)
    pg["sd_fp"] = (shifted.groupby(pg["player_id"])["fp_base"]
                   .rolling(20, min_periods=3).std().reset_index(level=0, drop=True).sort_index())

    pg["career_games"] = grp.cumcount()
    pg["season_games"] = pg.groupby(["player_id", "season_id"]).cumcount()
    pg["days_since"] = grp["game_date"].diff().dt.days
    prev_team = grp["team_id"].shift()
    pg["new_team"] = ((prev_team != pg["team_id"]) & prev_team.notna()).astype(int)
    # partite con la squadra attuale (si azzera al cambio squadra)
    pg["games_with_team"] = pg.groupby(["player_id", (pg["team_id"] != prev_team).cumsum()]).cumcount()
    return pg


def absences(pg, tg):
    """Per ogni squadra-partita: compagni "di rotazione" assenti e quanto pesavano.

    Un giocatore e' considerato assente in una partita della sua squadra se ha gia'
    giocato per quella squadra in stagione, non gioca questa, e (rigioca piu' avanti
    per la stessa squadra in stagione, oppure la sua ultima presenza e' recente).
    Nel modello in produzione gli assenti verranno dalla lista infortuni.
    Ritorna anche, per ogni giocatore-partita, quante partite della squadra ha saltato
    di fila prima di questa.
    """
    sched = tg[["game_id", "team_id", "season_id", "game_date"]].sort_values(["game_date", "game_id"])
    sched["tidx"] = sched.groupby(["team_id", "season_id"]).cumcount()
    played = pg[["player_id", "game_id", "team_id", "season_id", "m_l_min", "m_l_fp", "m_l_fga",
                 "m_l_ast", "position"]].merge(sched[["game_id", "team_id", "tidx"]],
                                                on=["game_id", "team_id"])
    played = played.sort_values(["player_id", "season_id", "tidx"])

    # tratti consecutivi del giocatore nella stessa squadra/stagione
    nxt = played.groupby(["player_id", "season_id"]).shift(-1)
    gaps = played[["player_id", "team_id", "season_id", "tidx", "m_l_min", "m_l_fp", "m_l_fga",
                   "m_l_ast", "position"]].copy()
    gaps["next_tidx"] = nxt["tidx"]
    gaps["next_team"] = nxt["team_id"]
    last = sched.groupby(["team_id", "season_id"])["tidx"].max().rename("last_tidx")
    gaps = gaps.join(last, on=["team_id", "season_id"])
    same = gaps["next_team"] == gaps["team_id"]
    # assente nelle partite tra questa presenza e la successiva (stessa squadra);
    # se non rigioca piu' per la squadra: assente per al massimo 15 partite
    gaps["end"] = np.where(same, gaps["next_tidx"] - 1,
                           np.where(gaps["next_team"].isna(),
                                    np.minimum(gaps["tidx"] + 15, gaps["last_tidx"]), gaps["tidx"]))
    gaps = gaps[gaps["end"] > gaps["tidx"]]
    gaps = gaps[gaps["m_l_min"] >= ROTATION_MIN]
    rep = gaps.loc[gaps.index.repeat((gaps["end"] - gaps["tidx"]).astype(int))].copy()
    rep["tidx"] = rep["tidx"] + rep.groupby(level=0).cumcount() + 1
    rep = rep.merge(sched[["team_id", "season_id", "tidx", "game_id"]],
                    on=["team_id", "season_id", "tidx"])

    rep["usage"] = rep["m_l_fga"] + rep["m_l_ast"]
    out = rep.groupby(["game_id", "team_id"]).agg(
        miss_n=("player_id", "size"), miss_min=("m_l_min", "sum"),
        miss_fp=("m_l_fp", "sum"), miss_usage=("usage", "sum")).reset_index()
    by_pos = (rep.pivot_table(index=["game_id", "team_id"], columns="position",
                              values="m_l_min", aggfunc="sum").add_prefix("miss_min_").reset_index())
    out = out.merge(by_pos, on=["game_id", "team_id"], how="outer")

    # partite saltate di fila prima di questa (dalla stessa tabella dei tratti)
    played["prev_tidx"] = played.groupby(["player_id", "season_id", "team_id"])["tidx"].shift()
    played["missed_before"] = (played["tidx"] - played["prev_tidx"] - 1).fillna(0)
    return out, rep[["game_id", "player_id"]], played[["player_id", "game_id", "missed_before"]]


def build():
    db = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    pg = load(db)
    print(f"partite giocatore: {len(pg):,}")
    tg = team_games(pg)
    pg = player_features(pg)
    # medie per partita (non per minuto) utili per gli assenti
    for c in ["fga", "ast"]:
        pg[f"m_l_{c}"] = pg[f"rl_{c}"] * pg["ml_min"]
    pg["m_l_min"] = pg["ml_min"]
    pg["m_l_fp"] = pg["ml_fp"]
    miss, _, missed_before = absences(pg, tg)
    pg = pg.drop(columns=["m_l_min", "m_l_fp", "m_l_fga", "m_l_ast"])

    pg = pg.merge(missed_before, on=["player_id", "game_id"], how="left")
    pg = pg.merge(miss, on=["game_id", "team_id"], how="left")
    miss_cols = [c for c in miss.columns if c.startswith("miss_")]
    pg[miss_cols] = pg[miss_cols].fillna(0)
    pg["miss_min_same_pos"] = np.select(
        [pg["position"] == p for p in "GFC"],
        [pg.get(f"miss_min_{p}", 0) for p in "GFC"], 0)

    team_cols = ["t_pace", "t_margin", "t_rest", "t_games_7d"]
    pg = pg.merge(tg[["game_id", "team_id"] + team_cols], on=["game_id", "team_id"], how="left")
    opp = tg[["game_id", "team_id", "t_pace", "t_margin", "t_allow_G", "t_allow_F", "t_allow_C",
              "t_rest"]].rename(columns=lambda c: c.replace("t_", "o_") if c.startswith("t_") else c)
    pg = pg.merge(opp.rename(columns={"team_id": "opponent_team_id"}),
                  on=["game_id", "opponent_team_id"], how="left")
    pg["o_allow_pos"] = np.select([pg["position"] == p for p in "GFC"],
                                  [pg[f"o_allow_{p}"] for p in "GFC"], np.nan)
    pg = pg.drop(columns=["o_allow_G", "o_allow_F", "o_allow_C"])
    pg["pos_code"] = pg["position"].map({"G": 0, "F": 1, "C": 2})
    pg["month"] = pg["game_date"].dt.month
    pg = pg.sort_values(["game_date", "game_id", "player_id"]).reset_index(drop=True)
    pg.to_parquet(OUT, index=False)
    print(f"salvato {OUT} ({len(pg):,} righe, {pg.shape[1]} colonne)")

    # controllo formula sul 2025/26
    s = pg[pg["season_id"] == 25]
    ok = (s["fp"] - s["pdk"]).abs() <= 0.01
    print(f"formula vs PDK 2025/26: {ok.mean():.2%} esatti")
    return pg


if __name__ == "__main__":
    build()
