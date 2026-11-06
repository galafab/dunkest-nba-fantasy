# Minuti di inizio stagione: bilancio di squadra per ruoli

Generato da `scripts/minuti_avvio_quote.py`. Stessa validazione in avanti.

`bilancio` = solo il modello strutturale; `lgbm + bilancio` = LightGBM con il bilancio come variabile
(previsioni fuori stagione sul training); `media` = 50% bilancio + 50% lgbm nuovo.

## Per stagione

| stagione   |    n |   lgbm |   lgbm nuovo |   bilancio |   lgbm + bilancio |   media bil. + lgbm nuovo |
|:-----------|-----:|-------:|-------------:|-----------:|------------------:|--------------------------:|
| 2021/22    |  405 |   4.65 |         4.68 |       4.75 |              4.62 |                      4.60 |
| 2022/23    |  426 |   4.30 |         4.26 |       4.51 |              4.27 |                      4.23 |
| 2023/24    |  448 |   4.06 |         3.98 |       4.43 |              3.95 |                      4.06 |
| 2024/25    |  438 |   4.31 |         4.22 |       4.46 |              4.19 |                      4.16 |
| 2025/26    |  426 |   4.27 |         4.19 |       4.54 |              4.13 |                      4.23 |
| TOTALE     | 2143 |   4.32 |         4.26 |       4.54 |              4.23 |                      4.26 |

## Per gruppo

| gruppo           |    n |   lgbm |   lgbm nuovo |   bilancio |   lgbm + bilancio |   media bil. + lgbm nuovo |
|:-----------------|-----:|-------:|-------------:|-----------:|------------------:|--------------------------:|
| cambiato squadra |  417 |   4.98 |         4.90 |       5.02 |              4.83 |                      4.88 |
| rookie           |  297 |   5.90 |         5.73 |       5.87 |              5.57 |                      5.67 |
| stessa squadra   | 1429 |   3.91 |         3.88 |       4.22 |              3.87 |                      3.88 |
| TOTALE           | 2143 |   4.32 |         4.26 |       4.54 |              4.23 |                      4.26 |

## Situazioni critiche

| gruppo                                   |    n |   lgbm |   lgbm nuovo |   bilancio |   lgbm + bilancio |   media bil. + lgbm nuovo |
|:-----------------------------------------|-----:|-------:|-------------:|-----------:|------------------:|--------------------------:|
| tutti                                    | 2143 |   4.32 |         4.26 |       4.54 |              4.23 |                      4.26 |
| unico del ruolo (dk)                     |   27 |   3.82 |         3.87 |       3.84 |              3.63 |                      3.72 |
| assente anno prima, con minuti 2 anni fa |   28 |   5.38 |         5.63 |       5.94 |              5.37 |                      5.71 |
| molti minuti liberati (vac_near>=40)     |  783 |   4.51 |         4.47 |       4.69 |              4.43 |                      4.45 |
| rookie                                   |  297 |   5.90 |         5.73 |       5.87 |              5.57 |                      5.67 |

## Classifica interna alla squadra

|                         |   Spearman medio per squadra |
|:------------------------|-----------------------------:|
| lgbm                    |                        0.831 |
| lgbm nuovo              |                        0.831 |
| bilancio                |                        0.828 |
| lgbm + bilancio         |                        0.828 |
| media bil. + lgbm nuovo |                        0.838 |

## Parametri del bilancio (ultimo allenamento, per 2025/26)

tau (larghezza fascia) = 0.86; lambda (peso del budget dell'anno prima) = 0.19; eps = 4.4; budget medio per fascia 1..5 = [52.4, 51.2, 49.2, 44.8, 42.5]

|             |   theta (pesi standardizzati) |
|:------------|------------------------------:|
| prior       |                          0.31 |
| rating      |                          0.02 |
| rookie      |                          0.08 |
| rk_logpick  |                         -0.12 |
| undrafted   |                         -0.01 |
| age         |                         -0.02 |
| age2        |                         -0.01 |
| new_team    |                         -0.01 |
| prev_start  |                         -0.01 |
| abs_est     |                         -0.04 |
| abs_unknown |                         -0.02 |
| healthy_min |                          0.06 |
