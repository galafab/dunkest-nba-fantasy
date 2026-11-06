# Modelli delle singole statistiche (generato da scripts/stat_modelli.py)

Test: 2025/26, 27,613 partite giocatore, minuti reali come esposizione.
Copertura = quota di partite con valore reale nell'intervallo 10-90% del modello; va confrontata con
la copertura attesa (dati discreti: non e' esattamente 80%).

## Conteggi

| statistica   |   base |    glmm |   lgbm |   lgbm+cal |
|:-------------|-------:|--------:|-------:|-----------:|
| fg2a         |  1.798 |   1.737 |  1.651 |      1.656 |
| tpa          |  1.358 |   1.329 |  1.268 |      1.269 |
| fta          |  1.571 |   1.532 |  1.515 |      1.523 |
| oreb         |  0.800 | nan     |  0.799 |      0.800 |
| dreb         |  1.358 | nan     |  1.339 |      1.341 |
| ast          |  1.243 | nan     |  1.199 |      1.198 |
| stl          |  0.680 | nan     |  0.682 |      0.684 |
| blk          |  0.498 | nan     |  0.491 |      0.488 |
| blka         |  0.516 | nan     |  0.505 |      0.502 |
| tov          |  0.859 | nan     |  0.844 |      0.845 |

Devianza Poisson media (piu' bassa = meglio):

| statistica   |   base |    glmm |   lgbm |   lgbm+cal |
|:-------------|-------:|--------:|-------:|-----------:|
| fg2a         |  1.255 |   1.205 |  1.092 |      1.094 |
| tpa          |  1.063 |   1.041 |  0.919 |      0.920 |
| fta          |  2.052 |   2.050 |  1.979 |      1.977 |
| oreb         |  1.121 | nan     |  1.102 |      1.102 |
| dreb         |  1.124 | nan     |  1.105 |      1.105 |
| ast          |  1.190 | nan     |  1.130 |      1.130 |
| stl          |  1.049 | nan     |  1.043 |      1.043 |
| blk          |  0.861 | nan     |  0.850 |      0.851 |
| blka         |  0.898 | nan     |  0.883 |      0.884 |
| tov          |  1.088 | nan     |  1.055 |      1.056 |

Copertura reale / attesa dell'intervallo 10-90%:

| statistica   | base          | glmm          | lgbm          | lgbm+cal      |
|:-------------|:--------------|:--------------|:--------------|:--------------|
| fg2a         | 88.1% / 88.0% | 86.3% / 88.7% | 88.3% / 88.7% | 88.4% / 88.7% |
| tpa          | 90.6% / 89.7% | 89.5% / 90.1% | 91.7% / 89.9% | 91.7% / 89.8% |
| fta          | 90.7% / 91.9% | 90.1% / 91.5% | 88.4% / 91.9% | 88.8% / 91.8% |
| oreb         | 93.8% / 94.1% | nan           | 94.2% / 94.1% | 94.2% / 94.0% |
| dreb         | 90.2% / 89.7% | nan           | 89.7% / 90.0% | 89.7% / 89.9% |
| ast          | 92.1% / 91.1% | nan           | 91.0% / 91.4% | 90.9% / 91.4% |
| stl          | 94.6% / 94.9% | nan           | 94.8% / 95.0% | 95.0% / 94.9% |
| blk          | 95.6% / 95.5% | nan           | 95.5% / 95.7% | 95.2% / 95.7% |
| blka         | 95.4% / 95.4% | nan           | 95.3% / 95.6% | 95.0% / 95.6% |
| tov          | 93.8% / 93.8% | nan           | 93.9% / 93.7% | 93.9% / 93.7% |

## Percentuali di tiro

| percentuale   | modello   |   log-loss |   MAE realizzati |   bias % |
|:--------------|:----------|-----------:|-----------------:|---------:|
| fg2           | base      |     0.6842 |           0.8919 |  -0.3776 |
| fg2           | lgbm      |     0.6826 |           0.8841 |  -0.5106 |
| fg2           | lgbm+cal  |     0.6826 |           0.8836 |  -0.2266 |
| tp            | base      |     0.6528 |           0.7596 |   0.3729 |
| tp            | lgbm      |     0.6509 |           0.7519 |  -0.0888 |
| tp            | lgbm+cal  |     0.6510 |           0.7523 |   0.1914 |
| ft            | base      |     0.5098 |           0.6540 |   0.2277 |
| ft            | lgbm      |     0.5091 |           0.6540 |  -0.3228 |
| ft            | lgbm+cal  |     0.5092 |           0.6548 |  -0.5054 |

## Calibrazione forza squadre (stimata sul 2024/25)

Fattore moltiplicativo exp(const + g x rating avversario + d x rating squadra) sulla previsione LightGBM
(percentuali: sul logit). Rating in punti di margine attesi.

| statistica   |   const |   o_rating |   t_rating |
|:-------------|--------:|-----------:|-----------:|
| fg2a         |  0.0119 |    -0.0006 |     0.0019 |
| tpa          |  0.0055 |    -0.0003 |    -0.0003 |
| fta          |  0.0325 |    -0.0025 |     0.0037 |
| oreb         |  0.0100 |    -0.0014 |     0.0010 |
| dreb         |  0.0054 |    -0.0004 |    -0.0001 |
| ast          | -0.0080 |    -0.0006 |     0.0026 |
| stl          |  0.0257 |    -0.0045 |    -0.0008 |
| blk          | -0.0299 |     0.0022 |    -0.0044 |
| blka         | -0.0358 |    -0.0034 |     0.0018 |
| tov          |  0.0051 |     0.0022 |    -0.0004 |
| fg2          |  0.0118 |    -0.0005 |     0.0003 |
| tp           |  0.0086 |     0.0032 |     0.0008 |
| ft           | -0.0079 |    -0.0020 |    -0.0016 |

## Elasticita' ai minuti (GLMM)

stat proporzionale a minuti^(1+beta) a parita' di tutto il resto; beta < 0 = meno che proporzionale.

| target   |   estimate |    se |   esponente minuti (1+beta) |
|:---------|-----------:|------:|----------------------------:|
| fg2a     |      0.014 | 0.004 |                       1.014 |
| tpa      |      0.001 | 0.005 |                       1.001 |
| fta      |      0.087 | 0.009 |                       1.087 |

## Famiglie e dispersione (GLMM)

| target   | family   |   dispersion |   sd_player |   sd_player_season |   minutes |
|:---------|:---------|-------------:|------------:|-------------------:|----------:|
| fg2a     | nbinom1  |        0.000 |       0.297 |              0.133 |    32.000 |
| tpa      | poisson  |      nan     |       0.649 |              0.178 |     6.300 |
| fta      | nbinom1  |        1.341 |       0.355 |              0.152 |    15.700 |
