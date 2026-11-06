# Minuti di inizio stagione (generato da scripts/minuti_avvio.py)

Obiettivo: minuti medi (su 240) nelle partite giocate tra le prime 10 della squadra.
Validazione in avanti: ogni stagione e' prevista con modelli allenati solo sugli avvii precedenti
(prima stagione prevista: 2021/22). Errore medio assoluto in minuti, pesato per
partite giocate. Bias = previsto - reale (positivo = sovrastima).

## Per stagione

| stagione   |    n |   ingenuo |   glm |   lgbm |   lgbm + chiusura 240 |
|:-----------|-----:|----------:|------:|-------:|----------------------:|
| 2021/22    |  405 |      4.72 |  4.71 |   4.65 |                  4.66 |
| 2022/23    |  426 |      4.66 |  4.33 |   4.33 |                  4.41 |
| 2023/24    |  448 |      4.26 |  4.38 |   4.07 |                  4.17 |
| 2024/25    |  438 |      4.40 |  4.26 |   4.31 |                  4.33 |
| 2025/26    |  426 |      4.72 |  4.39 |   4.25 |                  4.34 |
| TOTALE     | 2143 |      4.55 |  4.41 |   4.32 |                  4.38 |

## Per gruppo (tutte le stagioni)

| gruppo           |    n |   ingenuo |   glm |   lgbm |   lgbm + chiusura 240 |
|:-----------------|-----:|----------:|------:|-------:|----------------------:|
| cambiato squadra |  417 |      5.06 |  5.12 |   5.00 |                  5.03 |
| rookie           |  297 |      6.13 |  6.05 |   5.91 |                  5.84 |
| stessa squadra   | 1429 |      4.19 |  3.98 |   3.90 |                  3.99 |
| TOTALE           | 2143 |      4.55 |  4.41 |   4.32 |                  4.38 |

## Per fascia di minuti REALI

Attenzione: raggruppare per valore reale fa sembrare sovrastimati i bassi e sottostimati gli alti
(regressione verso la media); la tabella per minuti PREVISTI e' quella che conta in uso.

| fascia   |   n |   ingenuo MAE |   ingenuo bias |   glm MAE |   glm bias |   lgbm MAE |   lgbm bias |   lgbm + chiusura 240 MAE |   lgbm + chiusura 240 bias |
|:---------|----:|--------------:|---------------:|----------:|-----------:|-----------:|------------:|--------------------------:|---------------------------:|
| <10      | 507 |         +6.74 |          +6.28 |     +7.39 |      +7.12 |      +6.78 |       +6.56 |                     +6.71 |                      +6.47 |
| 10-20    | 564 |         +5.42 |          +2.28 |     +4.35 |      +2.01 |      +4.52 |       +1.86 |                     +4.44 |                      +1.71 |
| 20-30    | 580 |         +4.36 |          -0.93 |     +3.94 |      -1.85 |      +4.08 |       -1.29 |                     +4.08 |                      -1.39 |
| >30      | 492 |         +2.95 |          -1.93 |     +3.66 |      -3.07 |      +3.29 |       -2.58 |                     +3.60 |                      -2.91 |

## Per fascia di minuti PREVISTI (lgbm)

| fascia   |   n |   ingenuo MAE |   ingenuo bias |   glm MAE |   glm bias |   lgbm MAE |   lgbm bias |   lgbm + chiusura 240 MAE |   lgbm + chiusura 240 bias |
|:---------|----:|--------------:|---------------:|----------:|-----------:|-----------:|------------:|--------------------------:|---------------------------:|
| <10      | 270 |         +5.14 |          -1.46 |     +5.26 |      -0.35 |      +5.19 |       -2.05 |                     +5.19 |                      -2.09 |
| 10-20    | 750 |         +5.70 |          +0.73 |     +5.33 |      +0.78 |      +5.21 |       +0.31 |                     +5.20 |                      +0.30 |
| 20-30    | 693 |         +4.85 |          +0.96 |     +4.70 |      -0.27 |      +4.67 |       +0.51 |                     +4.64 |                      +0.36 |
| >30      | 430 |         +2.39 |          +0.43 |     +2.48 |      -0.47 |      +2.31 |       +0.31 |                     +2.62 |                      -0.18 |

## Coefficienti GLM (ultimo allenamento, per il 2025/26)

|                        |   coef |     p |
|:-----------------------|-------:|------:|
| Intercept              | 16.815 | 0.000 |
| prior                  |  0.711 | 0.000 |
| prev_min_last20        |  0.124 | 0.001 |
| prev_start             |  0.124 | 0.820 |
| prev_fp36              |  0.011 | 0.706 |
| prev_usage36           |  0.165 | 0.000 |
| prev_ts                |  5.105 | 0.052 |
| prev_missing           | -1.068 | 0.173 |
| age                    | -0.105 | 0.737 |
| I(age ** 2)            | -0.001 | 0.892 |
| rookie:I(np.log(pick)) | -0.006 | 0.985 |
| undrafted              | -0.730 | 0.028 |
| new_team               |  1.735 | 0.053 |
| comp_same              |  0.004 | 0.405 |
| comp_near              | -0.005 | 0.244 |
| log_rank               | -0.646 | 0.105 |
| team_sum               | -0.050 | 0.000 |
| prior:new_team         | -0.095 | 0.010 |

## Rookie: minuti medi nelle prime 10 partite per fascia di draft

| draft_bin   |   minuti |
|:------------|---------:|
| 1-5         |     28.3 |
| 6-14        |     17.6 |
| 15-30       |     11.8 |
| 31-60       |      7.2 |
| non scelto  |      7   |

## Importanza variabili (LightGBM, ultimo allenamento)

|                 |   quota |
|:----------------|--------:|
| prior           |   0.428 |
| prev_min        |   0.175 |
| prev_min_last20 |   0.08  |
| team_sum        |   0.053 |
| pick            |   0.037 |
| prev2_min       |   0.029 |
| age             |   0.028 |
| prev_usage36    |   0.024 |
| prev_gp         |   0.024 |
| comp_near       |   0.022 |
| comp_same       |   0.02  |
| prev_fp36       |   0.019 |
| prev_start      |   0.019 |
| prev_ts         |   0.014 |
| size            |   0.009 |
| roster_n        |   0.007 |
| rank_same       |   0.007 |
| new_team        |   0.002 |
| rookie          |   0.001 |
| undrafted       |   0     |
| prev_missing    |   0     |
