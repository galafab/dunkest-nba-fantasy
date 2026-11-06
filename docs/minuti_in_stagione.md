# Minuti in stagione: aggiornamento dopo ogni partita

Generato da `scripts/minuti_in_stagione.py`. Errore medio assoluto in minuti per la partita,
per numero di partite gia' giocate dal giocatore nella stagione (k). Stagioni di test 2022/23-2025/26,
ognuna prevista allenando solo sulle precedenti. Giocatori della rosa di inizio stagione, quando giocano.

- `inizio` = stima di inizio stagione ferma (come in produzione oggi)
- `ewm` = media esponenziale dei minuti della stagione
- `bayes` = (c * inizio + somma minuti) / (c + k)
- `lgbm` = LightGBM unico con minuti recenti, partenze, assenti di regime ecc.
- `separati` = un modello per la prima partita (LightGBM piccolo o lineare: inizio, inizio^2, assenti,
  partite della squadra) e un LightGBM per le partite successive, allenati su righe separate

## Per numero di partite giocate

| partite giocate   |     n |   inizio |   ewm |   bayes |   lgbm |   separati (k0 lgbm) |   separati (k0 lineare) |
|:------------------|------:|---------:|------:|--------:|-------:|---------------------:|------------------------:|
| 0 (prima)         |  1734 |     5.58 |  5.58 |    5.58 |   5.19 |                 5.20 |                    5.24 |
| 1                 |  1731 |     5.67 |  4.73 |    4.75 |   4.38 |                 4.38 |                    4.38 |
| 2                 |  1725 |     5.85 |  4.75 |    4.79 |   4.59 |                 4.60 |                    4.60 |
| 3-4               |  3430 |     5.91 |  4.64 |    4.69 |   4.44 |                 4.44 |                    4.44 |
| 5-9               |  8440 |     6.22 |  4.88 |    5.02 |   4.71 |                 4.71 |                    4.71 |
| 10-19             | 16295 |     6.50 |  4.91 |    5.20 |   4.81 |                 4.81 |                    4.81 |
| 20-39             | 28628 |     6.74 |  4.88 |    5.31 |   4.75 |                 4.75 |                    4.75 |
| 40+               | 30756 |     6.79 |  4.74 |    5.36 |   4.79 |                 4.79 |                    4.79 |
| TOTALE            | 92747 |     6.58 |  4.84 |    5.24 |   4.76 |                 4.76 |                    4.76 |

## Prima partita: medie per fascia della stima di inizio stagione (bias)

| fascia inizio   |      n |   reale |   inizio |   lgbm |   separati (k0 lgbm) |   separati (k0 lineare) |
|:----------------|-------:|--------:|---------:|-------:|---------------------:|------------------------:|
| (0, 10]         | 258.00 |    6.43 |     7.46 |   7.22 |                 6.68 |                    6.13 |
| (10, 20]        | 622.00 |   12.10 |    15.06 |  12.48 |                12.34 |                   12.43 |
| (20, 30]        | 507.00 |   23.57 |    24.94 |  22.61 |                22.79 |                   22.70 |
| (30, 45]        | 347.00 |   32.48 |    33.34 |  32.08 |                32.47 |                   32.42 |

## Per stagione

| stagione   |   inizio |   ewm |   bayes |   lgbm |   separati (k0 lgbm) |   separati (k0 lineare) |
|:-----------|---------:|------:|--------:|-------:|---------------------:|------------------------:|
| 2022/23    |     6.68 |  4.94 |    5.29 |   4.81 |                 4.81 |                    4.81 |
| 2023/24    |     6.50 |  4.80 |    5.25 |   4.69 |                 4.69 |                    4.69 |
| 2024/25    |     6.55 |  4.80 |    5.17 |   4.65 |                 4.65 |                    4.65 |
| 2025/26    |     6.58 |  4.80 |    5.26 |   4.87 |                 4.88 |                    4.88 |

c scelto per il bayes: {'2022/23': 2, '2023/24': 2, '2024/25': 2, '2025/26': 2}

## Importanza variabili (LightGBM partite successive, ultimo allenamento)

|             |   quota |
|:------------|--------:|
| ewm5        |   0.741 |
| ewm15       |   0.07  |
| last1       |   0.046 |
| last5       |   0.042 |
| last3       |   0.036 |
| miss_min    |   0.023 |
| start       |   0.012 |
| season_mean |   0.008 |
| t           |   0.007 |
| miss_n      |   0.006 |
| starter5    |   0.004 |
| played_frac |   0.003 |
| k           |   0.002 |
| home        |   0     |
