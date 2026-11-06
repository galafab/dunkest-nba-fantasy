"""
Rating dei giocatori (stile tesi, ricostruito dai box score).

Per ogni giocatore e stagione regolare:
  1. percentuali "alla Basketball Reference" (USG%, AST%, TRB%, STL%, BLK%, TOV%, TS%) e statistiche
     per 36 minuti, dai box score con i totali di squadra e avversari;
  2. PER (formula Hollinger: uPER, correzione per ritmo, normalizzata a 15 per stagione) e un
     "valore sopra il rimpiazzo" cumulato, (PER - 11) x minuti per partita, nello spirito di VORP/WS.
     BPM, WS e VORP originali non sono ricostruibili: niente plus/minus ne' difesa da tracking;
  3. macrocategorie come in tesi (ogni variabile riscalata min-max 60-100 sui giocatori con minuti
     sufficienti, poi media): Scoring, Playmaking, Rebounding, DifesaBox, Impatto, TempoUso
     (minuti per partita e USG%). Overall = media di tutte tranne Rebounding; PlayerRating = media di
     Overall e Impatto, riscalata min-max 60-100 (una sola volta, alla fine).

Rating "di avvio" per una stagione S = media pesata (recenza x minuti) delle 3 stagioni precedenti,
ridotta verso un prior (per i rookie: media del rating al primo anno per fascia di draft, calcolata
solo su stagioni < S). Niente informazione dal futuro.

    python rating_giocatori.py   -> data/rating_stagione.parquet, data/rating_avvio.parquet,
                                    docs/rating_giocatori.md
"""

import sqlite3

import numpy as np
import pandas as pd

from stima_dati import DB, ROOT
from minuti_avvio import SEASONS, NEW_SEASON, PREV_OF, DRAFT_BINS, DRAFT_LABELS, load_bio

REPORT = ROOT / "docs" / "rating_giocatori.md"
OUT_SEASON = ROOT / "data" / "rating_stagione.parquet"
OUT_START = ROOT / "data" / "rating_avvio.parquet"
MIN_QUAL = 500          # minuti stagionali minimi per entrare nelle scale e nell'allenamento
REPL_PER = 11.0                   # PER del giocatore di rimpiazzo (convenzione VORP)
DECAY = [1.0, 0.6, 0.35]          # peso per 1, 2, 3 stagioni fa
SHRINK_MIN = 600                  # "minuti equivalenti" del prior
RATE_COLS = ["pts", "oreb", "dreb", "ast", "stl", "blk", "tov", "fga", "fta", "tpm", "fgm", "ftm"]


# --------------------------------------------------------------------------- box score

def load_games(db):
    pg = pd.read_sql(f"""
        SELECT game_id, player_id, season_id, team_id, minutes AS mp, starter,
               pts, oreb, dreb, ast, stl, blk, tov, pf, fgm, fga, tpm, tpa, ftm, fta
        FROM player_games
        WHERE minutes > 0 AND season_id IN ({",".join(map(str, SEASONS))})""", db)
    tm = pg.groupby(["game_id", "team_id"]).agg(
        tm_mp=("mp", "sum"), tm_fga=("fga", "sum"), tm_fta=("fta", "sum"), tm_tov=("tov", "sum"),
        tm_oreb=("oreb", "sum"), tm_dreb=("dreb", "sum"), tm_fgm=("fgm", "sum"), tm_tpa=("tpa", "sum"),
        tm_ast=("ast", "sum")).reset_index()
    tm["tm_poss"] = tm["tm_fga"] + 0.44 * tm["tm_fta"] - tm["tm_oreb"] + tm["tm_tov"]
    opp = tm[["game_id", "team_id", "tm_fga", "tm_tpa", "tm_oreb", "tm_dreb", "tm_poss"]]
    opp = opp.rename(columns={c: c.replace("tm_", "op_") for c in opp.columns if c.startswith("tm_")})
    opp = opp.rename(columns={"team_id": "opp_id"})
    g = tm.merge(opp, on="game_id")
    g = g[g["team_id"] != g["opp_id"]]
    pg = pg.merge(g.drop(columns="opp_id"), on=["game_id", "team_id"])
    pg["len"] = pg["tm_mp"] / 5                                   # durata partita in minuti
    pg["share"] = pg["mp"] / pg["len"]                            # quota di partita in campo
    pg["pace"] = 48 * (pg["tm_poss"] + pg["op_poss"]) / 2 / pg["len"]
    return pg


def add_per(pg, s):
    """PER di Hollinger (come su Basketball Reference) per ogni giocatore e stagione.
    Costanti di lega e rapporti di squadra per stagione; correzione per ritmo di squadra."""
    lg = pg.groupby("season_id")[["pts", "fga", "fta", "fgm", "ftm", "ast", "oreb", "dreb", "tov", "pf"]].sum()
    lg["trb"] = lg["oreb"] + lg["dreb"]
    lg["factor"] = 2 / 3 - 0.5 * (lg["ast"] / lg["fgm"]) / (2 * (lg["fgm"] / lg["ftm"]))
    lg["vop"] = lg["pts"] / (lg["fga"] - lg["oreb"] + lg["tov"] + 0.44 * lg["fta"])
    lg["drbp"] = (lg["trb"] - lg["oreb"]) / lg["trb"]
    lg["lg_pace"] = pg.groupby("season_id").apply(lambda x: (x["pace"] * x["mp"]).sum() / x["mp"].sum(),
                                                 include_groups=False)
    # rapporto assist/canestri e ritmo della squadra, per stagione
    ts = pg.drop_duplicates(["game_id", "team_id"]).groupby(["season_id", "team_id"]).agg(
        t_ast=("tm_ast", "sum"), t_fgm=("tm_fgm", "sum"), t_pace=("pace", "mean"))
    d = pg.join(lg, on="season_id", rsuffix="_lg").join(ts, on=["season_id", "team_id"])
    r = d["t_ast"] / d["t_fgm"]
    trb = d["oreb"] + d["dreb"]
    c = (d["tpm"] + 2 / 3 * d["ast"] + (2 - d["factor"] * r) * d["fgm"]
         + d["ftm"] * 0.5 * (1 + (1 - r) + 2 / 3 * r)
         - d["vop"] * d["tov"] - d["vop"] * d["drbp"] * (d["fga"] - d["fgm"])
         - d["vop"] * 0.44 * (0.44 + 0.56 * d["drbp"]) * (d["fta"] - d["ftm"])
         + d["vop"] * (1 - d["drbp"]) * (trb - d["oreb"]) + d["vop"] * d["drbp"] * d["oreb"]
         + d["vop"] * d["stl"] + d["vop"] * d["drbp"] * d["blk"]
         - d["pf"] * (d["ftm_lg"] / d["pf_lg"] - 0.44 * d["fta_lg"] / d["pf_lg"] * d["vop"]))
    d["uper_num"] = c
    d["aper_num"] = c * d["lg_pace"] / d["t_pace"]
    g = d.groupby(["player_id", "season_id"])[["aper_num", "mp"]].sum()
    aper = g["aper_num"] / g["mp"]
    lg_aper = (g["aper_num"].groupby("season_id").sum() / g["mp"].groupby("season_id").sum())
    per = aper * 15 / aper.index.get_level_values("season_id").map(lg_aper).values
    return s.merge(per.rename("per").reset_index(), on=["player_id", "season_id"], how="left")


def season_stats(pg):
    """Una riga per giocatore e stagione (tutte le squadre insieme)."""
    d = pg.copy()
    plays = d["fga"] + 0.44 * d["fta"] + d["tov"]
    tm_plays = d["tm_fga"] + 0.44 * d["tm_fta"] + d["tm_tov"]
    cols = {
        "usg_n": plays * d["len"], "usg_d": d["mp"] * tm_plays,
        "orb_n": d["oreb"] * d["len"], "orb_d": d["mp"] * (d["tm_oreb"] + d["op_dreb"]),
        "drb_n": d["dreb"] * d["len"], "drb_d": d["mp"] * (d["tm_dreb"] + d["op_oreb"]),
        "stl_n": d["stl"] * d["len"], "stl_d": d["mp"] * d["op_poss"],
        "blk_n": d["blk"] * d["len"], "blk_d": d["mp"] * (d["op_fga"] - d["op_tpa"]),
        "ast_d": d["share"] * d["tm_fgm"] - d["fgm"], "plays": plays,
    }
    for k, v in cols.items():
        d[k] = v
    sum_cols = ["mp", "starter", "plays"] + RATE_COLS + [c for c in cols if c.endswith(("_n", "_d"))]
    g = d.groupby(["player_id", "season_id"])
    s = g[sum_cols].sum()
    s["gp"] = g.size()
    s["last_team"] = g["team_id"].last()
    s = s.reset_index()
    pct = lambda n, dd: 100 * s[n] / s[dd]
    s["usg"], s["orb"], s["drb"] = pct("usg_n", "usg_d"), pct("orb_n", "orb_d"), pct("drb_n", "drb_d")
    s["stl_p"], s["blk_p"] = pct("stl_n", "stl_d"), pct("blk_n", "blk_d")
    s["ast_p"] = 100 * s["ast"] / s["ast_d"].clip(lower=1)
    s["tov_p"] = 100 * s["tov"] / s["plays"].clip(lower=1)
    s["ts"] = s["pts"] / (2 * (s["fga"] + 0.44 * s["fta"])).replace(0, np.nan)
    s["pts36"] = s["pts"] / s["mp"] * 36
    s["mpg"] = s["mp"] / s["gp"]
    s = add_per(pg, s)
    s["vorp_pg"] = (s["per"] - REPL_PER) * s["mpg"] / 36          # valore sopra il rimpiazzo, per partita
    return s


# --------------------------------------------------------------------------- rating di stagione

# (variabile, segno). Come in tesi: minuti per partita e USG% stanno in "TempoUso".
SCALE_VARS = {
    "Scoring": [("pts36", 1), ("ts", 1)],
    "Playmaking": [("ast_p", 1), ("tov_p", -1)],
    "Rebounding": [("orb", 1), ("drb", 1)],
    "DifesaBox": [("stl_p", 1), ("blk_p", 1)],
    "Impatto": [("per", 1), ("vorp_pg", 1)],
    "TempoUso": [("mpg", 1), ("usg", 1)],
}
OVERALL = ["Scoring", "Playmaking", "DifesaBox", "Impatto", "TempoUso"]     # senza Rebounding, come in tesi


def minmax(x, qual, sign=1):
    """60-100 con minimo e massimo osservati fra i giocatori con minuti sufficienti (tutte le stagioni)."""
    lo, hi = x[qual].min(), x[qual].max()
    z = ((x - lo) / (hi - lo)).clip(0, 1)
    return 60 + 40 * (z if sign > 0 else 1 - z)


def season_rating(s):
    qual = s["mp"] >= MIN_QUAL
    for macro, vs in SCALE_VARS.items():
        s[macro] = pd.concat([minmax(s[c], qual, sg) for c, sg in vs], axis=1).mean(axis=1)
    s["Overall"] = s[OVERALL].mean(axis=1)
    s["PlayerRating"] = minmax((s["Overall"] + s["Impatto"]) / 2, qual)
    return s


# --------------------------------------------------------------------------- rating di avvio

def draft_prior(s, bio, season):
    """Rating medio al primo anno per fascia di draft, solo stagioni < season."""
    first = s.sort_values("season_id").drop_duplicates("player_id")
    first = first[first["season_id"] < season].merge(bio[["player_id", "pick"]], on="player_id", how="left")
    first["bin"] = pd.cut(first["pick"].fillna(99), DRAFT_BINS, labels=DRAFT_LABELS).astype(str)
    pri = first.groupby("bin")["PlayerRating"].mean()
    # chi esordisce con pochi minuti ha un rating poco affidabile: la media e' comunque bassa, va bene
    return pri.to_dict(), first["PlayerRating"].mean()


def start_ratings(s, bio):
    rows = []
    for season in SEASONS + [NEW_SEASON]:
        prev = []
        p = season
        for _ in DECAY:
            p = PREV_OF.get(p)
            if p is None:
                break
            prev.append(p)
        if not prev:
            continue
        hist = s[s["season_id"].isin(prev)].copy()
        hist["lag"] = hist["season_id"].map({p: i for i, p in enumerate(prev)})
        hist["w"] = hist["lag"].map(lambda i: DECAY[i]) * hist["mp"]
        hist = hist.dropna(subset=["PlayerRating"])
        pri, mean_all = draft_prior(s, bio, season)
        b = bio.set_index("player_id")["pick"]
        # prior del giocatore: fascia di draft (se nota) altrimenti media dei "non scelti"
        def prior_of(pid):
            pick = b.get(pid, np.nan)
            lab = str(pd.cut([99 if pd.isna(pick) else pick], DRAFT_BINS, labels=DRAFT_LABELS)[0])
            return pri.get(lab, mean_all)
        g = hist.groupby("player_id")
        agg = pd.DataFrame({"wsum": g["w"].sum(),
                            "wr": (hist["w"] * hist["PlayerRating"]).groupby(hist["player_id"]).sum(),
                            "n_prev": g.size(), "last_lag": g["lag"].min()})
        agg["prior"] = [prior_of(pid) for pid in agg.index]
        agg["rating_avvio"] = (agg["wr"] + SHRINK_MIN * agg["prior"]) / (agg["wsum"] + SHRINK_MIN)
        agg["affidabilita"] = agg["wsum"] / (agg["wsum"] + SHRINK_MIN)
        agg["season_id"] = season
        rows.append(agg.reset_index()[["player_id", "season_id", "rating_avvio", "affidabilita",
                                       "n_prev", "last_lag"]])
        rows[-1].attrs["draft_prior"] = pri
    return pd.concat(rows, ignore_index=True), pri


# --------------------------------------------------------------------------- validazione e rapporto

def validate(s, start):
    """Il rating di avvio di S spiega i minuti della stagione S, dentro la squadra?"""
    cur = s[["player_id", "season_id", "mp", "gp", "last_team"]].copy()
    cur["min_g"] = cur["mp"] / cur["gp"]
    sv = s.assign(min_g=s["mp"] / s["gp"])[["player_id", "season_id", "min_g"]]
    sv = sv.assign(season_id=sv["season_id"].map({v: k for k, v in PREV_OF.items()}))
    sv = sv.rename(columns={"min_g": "prev_min"}).dropna(subset=["season_id"])
    sv["season_id"] = sv["season_id"].astype(int)
    d = cur.merge(start, on=["player_id", "season_id"]).merge(sv, on=["player_id", "season_id"], how="left")
    d = d[(d["gp"] >= 20) & (d["season_id"].isin(SEASONS[1:]))]
    out = []
    for season, x in d.groupby("season_id"):
        # rango dentro la squadra: Spearman fra rating_avvio e minuti, e fra prev_min e minuti
        def within(col):
            vals = []
            for _, t in x.groupby("last_team"):
                if len(t) >= 6 and t[col].notna().sum() >= 6:
                    vals.append(t[[col, "min_g"]].corr(method="spearman").iloc[0, 1])
            return np.nanmean(vals)
        out.append({"stagione": season, "n": len(x),
                    "rho_rating_avvio": within("rating_avvio"), "rho_prev_min": within("prev_min"),
                    "corr_rating_min": x[["rating_avvio", "min_g"]].corr().iloc[0, 1]})
    return pd.DataFrame(out)


def write_report(s, start, val, names):
    L = ["# Rating dei giocatori (ricostruito dai box score)", "",
         "Generato da `scripts/rating_giocatori.py`. Versione semplificata del rating della tesi:",
         "PER ricostruito con la formula di Hollinger; al posto di BPM/WS/VORP un valore sopra il rimpiazzo",
         "cumulato, (PER - 11) x minuti per partita / 36. Niente difesa da tracking (tiri contestati, % avversari).", "",
         "## Validazione del rating di avvio (rango dentro la squadra)", "",
         "Spearman medio per squadra fra i minuti per partita della stagione S (giocatori con >= 20 partite)",
         "e (a) rating di avvio calcolato solo sulle stagioni precedenti, (b) minuti per partita dell'anno prima.", "",
         val.round(3).to_markdown(index=False), ""]
    last = s[s["season_id"] == SEASONS[-1]].merge(names, on="player_id", how="left")
    top = last[last["mp"] >= 800].nlargest(30, "PlayerRating")
    L += ["## Top 30 PlayerRating 2025/26 (>= 800 minuti)", "",
          top[["name", "mp", "per", "Scoring", "Playmaking", "Rebounding", "DifesaBox", "Impatto", "TempoUso",
               "Overall", "PlayerRating"]].round(1).to_markdown(index=False), ""]
    REPORT.write_text("\n".join(L), encoding="utf-8")


def main():
    db = sqlite3.connect(DB)
    pg = load_games(db)
    s = season_stats(pg)
    s = season_rating(s)
    bio = load_bio(db)
    start, pri = start_ratings(s, bio)
    names = pd.read_sql("SELECT player_id, first_name || ' ' || last_name AS name FROM players", db)
    val = validate(s, start)
    s.to_parquet(OUT_SEASON)
    start.to_parquet(OUT_START)
    write_report(s, start, val, names)
    print(val.round(3).to_string(index=False))
    print("prior draft:", {k: round(v, 1) for k, v in pri.items()})


if __name__ == "__main__":
    main()
