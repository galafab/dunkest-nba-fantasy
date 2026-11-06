"""
Verifica: il modello riproduce l'effetto della forza dell'avversario sul punteggio?

Sul 2025/26 (previsioni LightGBM salvate da stat_modelli.py, minuti reali), per i giocatori con media
>= 30 punti Dunkest: punteggio previsto e reale rispetto alla media del giocatore, per fascia di
forza dell'avversario (rating dalle rose). Punteggio "lineare": formula Dunkest senza bonus doppie,
triple e vittoria (cosi' si confronta solo l'effetto sulle statistiche).

    python verifica_avversario.py
"""

import pandas as pd

from stat_modelli import PRED_TEST
from stima_dati import OUT as BASE_DATASET

STAR_FP = 30


def linear_fp(d, pre=""):
    g = lambda c: d[f"{pre}{c}"]
    if pre:
        fg2m, tpm, ftm = g("fg2a") * g("p_fg2"), g("tpa") * g("p_tp"), g("fta") * g("p_ft")
    else:
        fg2m, tpm, ftm = d["fg2m"], d["tpm"], d["ftm"]
    pts = 2 * fg2m + 3 * tpm + ftm
    return (pts + g("dreb") + 1.25 * g("oreb") + 1.5 * g("ast") + 1.5 * g("stl") - 1.5 * g("tov") + 1.5 * g("blk")
            - 0.5 * g("blka") - (g("fg2a") + g("tpa") - fg2m - tpm) - (g("fta") - ftm) + d["starter"])


def main():
    p = pd.read_parquet(PRED_TEST)
    b = pd.read_parquet(BASE_DATASET, columns=["game_id", "player_id", "ml_fp"])
    d = p.merge(b, on=["game_id", "player_id"])
    d = d[d["ml_fp"] >= STAR_FP].copy()
    d["reale"] = linear_fp(d)
    d["lgbm"] = linear_fp(d, "lgbm_")
    d["lgbm+cal"] = linear_fp(d, "cal_")
    d["avversario"] = pd.qcut(d["o_rating"], 5, labels=["1 piu' scarsi", "2", "3", "4", "5 piu' forti"])
    t = d.groupby("avversario", observed=True).agg(partite=("reale", "size"), rating=("o_rating", "mean"),
                                                   reale=("reale", "mean"), lgbm=("lgbm", "mean"),
                                                   cal=("lgbm+cal", "mean"))
    for c, col in (("reale", "reale"), ("lgbm", "lgbm"), ("cal", "lgbm+cal")):
        t[f"{c} vs media %"] = (t[c] / d[col].mean() - 1) * 100
    print(f"giocatori da {STAR_FP}+ punti, 2025/26: {len(d)} partite")
    print(t.round(2).to_string())


if __name__ == "__main__":
    main()
