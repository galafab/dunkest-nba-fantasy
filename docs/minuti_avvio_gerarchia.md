# Minuti di inizio stagione: rating, gerarchia e minuti liberati

Generato da `scripts/minuti_avvio_gerarchia.py`. Stessa validazione in avanti di `minuti_avvio.py`.
`glm`/`lgbm` = variabili originali; `... nuovo` = con rating, gerarchia, minuti liberati, assenza.
Errore medio assoluto in minuti (su 240), pesato per partite giocate.

## Per stagione

| stagione   |    n |   ingenuo |   glm |   lgbm |   lgbm + 240 |   glm nuovo |   lgbm nuovo |   lgbm nuovo + 240 |
|:-----------|-----:|----------:|------:|-------:|-------------:|------------:|-------------:|-------------------:|
| 2021/22    |  405 |      4.72 |  4.71 |   4.65 |         4.66 |        4.66 |         4.68 |               4.67 |
| 2022/23    |  426 |      4.66 |  4.33 |   4.33 |         4.41 |        4.23 |         4.28 |               4.39 |
| 2023/24    |  448 |      4.26 |  4.38 |   4.07 |         4.17 |        4.25 |         4.01 |               4.21 |
| 2024/25    |  438 |      4.40 |  4.26 |   4.31 |         4.33 |        4.27 |         4.17 |               4.21 |
| 2025/26    |  426 |      4.72 |  4.39 |   4.25 |         4.34 |        4.26 |         4.20 |               4.26 |
| TOTALE     | 2143 |      4.55 |  4.41 |   4.32 |         4.38 |        4.33 |         4.27 |               4.35 |

## Per gruppo

| gruppo           |    n |   ingenuo |   glm |   lgbm |   lgbm + 240 |   glm nuovo |   lgbm nuovo |   lgbm nuovo + 240 |
|:-----------------|-----:|----------:|------:|-------:|-------------:|------------:|-------------:|-------------------:|
| cambiato squadra |  417 |      5.06 |  5.12 |   5.00 |         5.03 |        5.04 |         4.92 |               4.96 |
| rookie           |  297 |      6.13 |  6.05 |   5.91 |         5.84 |        5.73 |         5.72 |               5.67 |
| stessa squadra   | 1429 |      4.19 |  3.98 |   3.90 |         3.99 |        3.94 |         3.88 |               3.99 |
| TOTALE           | 2143 |      4.55 |  4.41 |   4.32 |         4.38 |        4.33 |         4.27 |               4.35 |

## Situazioni critiche

| gruppo                                   |    n |   ingenuo |   lgbm |   lgbm nuovo |   lgbm + 240 |   lgbm nuovo + 240 |
|:-----------------------------------------|-----:|----------:|-------:|-------------:|-------------:|-------------------:|
| tutti                                    | 2143 |      4.55 |   4.32 |         4.27 |         4.38 |               4.35 |
| unico del ruolo (dk)                     |   27 |      3.84 |   3.84 |         3.82 |         3.72 |               3.63 |
| primo per rating nel ruolo               |  423 |      2.86 |   2.75 |         2.66 |         2.97 |               2.93 |
| assente anno prima, con minuti 2 anni fa |   28 |      8.29 |   5.16 |         5.66 |         5.80 |               6.23 |
| assente anno prima, mai visto            |    8 |     11.09 |   9.55 |         9.13 |         9.74 |               9.34 |
| pochi match anno prima (<40)             |  370 |      5.31 |   4.82 |         4.81 |         4.84 |               4.82 |
| molti minuti liberati (vac_near>=40)     |  783 |      4.80 |   4.50 |         4.46 |         4.57 |               4.55 |
| rookie                                   |  297 |      6.13 |   5.91 |         5.72 |         5.84 |               5.67 |

## Classifica interna alla squadra

Spearman medio fra minuti previsti e reali dentro la stessa squadra e stagione.

|                  |   Spearman medio per squadra |
|:-----------------|-----------------------------:|
| ingenuo          |                        0.815 |
| glm              |                        0.822 |
| lgbm             |                        0.832 |
| lgbm + 240       |                        0.832 |
| glm nuovo        |                        0.826 |
| lgbm nuovo       |                        0.834 |
| lgbm nuovo + 240 |                        0.834 |

## Importanza variabili (LightGBM nuovo, ultimo allenamento)

|                 |   quota |
|:----------------|--------:|
| prior           |   0.452 |
| prev_min        |   0.105 |
| prev_min_last20 |   0.08  |
| team_sum        |   0.042 |
| healthy_min     |   0.025 |
| rating          |   0.022 |
| pick            |   0.021 |
| age             |   0.021 |
| prev2_min       |   0.019 |
| prev_gp         |   0.016 |
| dist_team       |   0.015 |
| gap_near        |   0.015 |
| rating_aff      |   0.014 |
| vac_near_share  |   0.014 |
| prev_usage36    |   0.013 |
| gap_same        |   0.013 |
| comp_near       |   0.012 |
| vac_near        |   0.012 |
| comp_same       |   0.01  |
| prev_start      |   0.01  |
| prev_fp36       |   0.01  |
| vac_same        |   0.01  |
| vac_total       |   0.009 |
| prev_ts         |   0.009 |
| better_near     |   0.007 |
| size            |   0.006 |
| roster_n        |   0.004 |
| rank_team       |   0.003 |
| better_same     |   0.003 |
| rank_same       |   0.003 |
| same_count      |   0.002 |
| new_team        |   0.002 |
| rookie          |   0     |
| undrafted       |   0     |
| prev_missing    |   0     |
| alone_same      |   0     |
| abs_est         |   0     |
| abs_unknown     |   0     |

## Coefficienti GLM nuovo (ultimo allenamento)

|                        |    coef |     p |
|:-----------------------|--------:|------:|
| Intercept              |   1.532 | 0.800 |
| prior                  |   0.449 | 0.000 |
| prev_min_last20        |   0.177 | 0.000 |
| prev_start             |   0.778 | 0.153 |
| prev_fp36              |  -0.110 | 0.007 |
| prev_usage36           |   0.054 | 0.196 |
| prev_ts                |   3.791 | 0.149 |
| prev_missing           |   3.107 | 0.016 |
| age                    |   0.394 | 0.218 |
| I(age ** 2)            |  -0.009 | 0.098 |
| rookie:I(np.log(pick)) |  -2.110 | 0.000 |
| undrafted              |  -0.174 | 0.604 |
| new_team               |   0.999 | 0.258 |
| comp_same              |   0.002 | 0.669 |
| comp_near              |   0.002 | 0.746 |
| log_rank               |  -0.361 | 0.362 |
| team_sum               |  -0.046 | 0.000 |
| prior:new_team         |  -0.080 | 0.027 |
| rating                 |   0.180 | 0.009 |
| gap_near               |   0.030 | 0.197 |
| better_near            |  -0.451 | 0.000 |
| alone_same             |  -0.191 | 0.843 |
| vac_near               |   0.025 | 0.018 |
| vac_near_share         |   0.008 | 0.997 |
| abs_est                |  -9.448 | 0.000 |
| abs_unknown            | -10.613 | 0.001 |
| healthy_min            |   0.134 | 0.070 |
