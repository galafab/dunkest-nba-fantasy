# Analisi minuti (generato da scripts/analisi_minuti.py)

Stagioni 2018/19-2024/25, giocatori con almeno 10 partite di storico: 162,178 partite.

## 1. Previsione con le sole medie del giocatore

MAE in minuti (su 240), R2. Combinazione: Intercept +1.41, ms_min +0.71, ml_min -0.03, prev_filled +0.03, last_min +0.25

| previsione          |   ('MAE', '1-10') |   ('MAE', '11-30') |   ('MAE', '31+') |   ('R2', '1-10') |   ('R2', '11-30') |   ('R2', '31+') |
|:--------------------|------------------:|-------------------:|-----------------:|-----------------:|------------------:|----------------:|
| combinazione (OLS)  |              5.32 |               5.03 |             4.85 |             0.61 |              0.63 |            0.57 |
| media lunga (25)    |              5.98 |               5.67 |             5.34 |             0.51 |              0.54 |            0.49 |
| media recente (5)   |              5.51 |               5.13 |             4.90 |             0.57 |              0.61 |            0.56 |
| stagione precedente |              6.37 |               6.41 |             6.34 |             0.44 |              0.41 |            0.27 |

Errore della combinazione per fascia di minuti abituali (MAE):

| fascia   |   1-10 |   11-30 |   31+ |
|:---------|-------:|--------:|------:|
| <15      |   6.00 |    5.74 |  6.00 |
| 15-28    |   5.69 |    5.22 |  5.16 |
| >28      |   4.26 |    4.11 |  4.07 |

## 2. Scarti: minuti reali - combinazione delle medie

Scarto medio positivo = gioca piu' del solito. IC al 95% raggruppati per giocatore.

### Minuti abituali dei compagni assenti nello STESSO ruolo (G/F/C)

| gruppo   | fascia   |     n |   scarto medio | IC95           |
|:---------|:---------|------:|---------------:|:---------------|
| C        | 0        | 18479 |          -0.84 | [-1.03, -0.64] |
| C        | 0-15     |  2181 |          +0.13 | [-0.19, +0.45] |
| C        | 15-30    |  4350 |          +1.26 | [+0.96, +1.56] |
| C        | 30-45    |  1611 |          +2.78 | [+2.20, +3.37] |
| C        | >45      |   409 |          +4.69 | [+3.55, +5.84] |
| F        | 0        | 22765 |          -1.04 | [-1.17, -0.91] |
| F        | 0-15     |  4033 |          -0.48 | [-0.73, -0.22] |
| F        | 15-30    | 15203 |          -0.14 | [-0.26, -0.01] |
| F        | 30-45    | 10329 |          +0.52 | [+0.38, +0.66] |
| F        | >45      |  9717 |          +2.16 | [+1.96, +2.36] |
| G        | 0        | 21036 |          -1.43 | [-1.57, -1.28] |
| G        | 0-15     |  5395 |          -0.86 | [-1.11, -0.61] |
| G        | 15-30    | 17557 |          -0.30 | [-0.44, -0.17] |
| G        | 30-45    | 14216 |          +0.54 | [+0.42, +0.67] |
| G        | >45      | 14897 |          +2.48 | [+2.30, +2.65] |

### Minuti abituali dei compagni assenti negli ALTRI ruoli

| gruppo   | fascia   |     n |   scarto medio | IC95           |
|:---------|:---------|------:|---------------:|:---------------|
| tutti    | 0        | 29147 |          -1.26 | [-1.36, -1.16] |
| tutti    | 0-20     | 22100 |          -0.77 | [-0.88, -0.66] |
| tutti    | 20-40    | 42509 |          -0.35 | [-0.42, -0.28] |
| tutti    | 40-60    | 33252 |          +0.34 | [+0.25, +0.42] |
| tutti    | >60      | 35170 |          +1.63 | [+1.52, +1.74] |

### Differenza di forza attesa (margine medio squadra - avversario; + = favorita)

| gruppo   | fascia   |     n |   scarto medio | IC95           |
|:---------|:---------|------:|---------------:|:---------------|
| riserva  | < -9     | 10924 |          +0.49 | [+0.33, +0.65] |
| riserva  | -9/-6    |  7813 |          +0.20 | [+0.04, +0.37] |
| riserva  | -6/-3    | 10475 |          -0.11 | [-0.24, +0.03] |
| riserva  | -3/+3    | 25586 |          -0.34 | [-0.43, -0.25] |
| riserva  | +3/+6    | 10844 |          -0.47 | [-0.60, -0.34] |
| riserva  | +6/+9    |  8280 |          -0.29 | [-0.44, -0.15] |
| riserva  | > +9     | 12628 |          -0.01 | [-0.13, +0.12] |
| titolare | < -9     | 10017 |          +0.62 | [+0.45, +0.78] |
| titolare | -9/-6    |  7039 |          +0.21 | [+0.05, +0.37] |
| titolare | -6/-3    |  9231 |          +0.12 | [-0.01, +0.25] |
| titolare | -3/+3    | 22522 |          +0.18 | [+0.09, +0.26] |
| titolare | +3/+6    |  9317 |          +0.22 | [+0.11, +0.33] |
| titolare | +6/+9    |  7102 |          +0.05 | [-0.09, +0.18] |
| titolare | > +9     | 10308 |          -0.46 | [-0.57, -0.34] |

### Margine finale effettivo (solo descrittivo: garbage time)

| gruppo   | fascia   |     n |   scarto medio | IC95           |
|:---------|:---------|------:|---------------:|:---------------|
| riserva  | 0-5      | 19544 |          -0.13 | [-0.24, -0.02] |
| riserva  | 5-10     | 21009 |          -0.09 | [-0.18, +0.01] |
| riserva  | 10-20    | 26881 |          -0.52 | [-0.61, -0.44] |
| riserva  | 20-30    | 12727 |          -0.02 | [-0.16, +0.12] |
| riserva  | >30      |  6407 |          +1.22 | [+0.97, +1.46] |
| titolare | 0-5      | 19287 |          +1.33 | [+1.21, +1.45] |
| titolare | 5-10     | 20092 |          +1.00 | [+0.90, +1.10] |
| titolare | 10-20    | 21999 |          -0.11 | [-0.19, -0.03] |
| titolare | 20-30    |  9599 |          -1.95 | [-2.11, -1.79] |
| titolare | >30      |  4591 |          -3.09 | [-3.36, -2.82] |

### Giorni di riposo della squadra

| gruppo   | fascia   |     n |   scarto medio | IC95           |
|:---------|:---------|------:|---------------:|:---------------|
| riserva  | 1 (b2b)  | 15499 |          +0.96 | [+0.84, +1.08] |
| riserva  | 2        | 54393 |          -0.26 | [-0.32, -0.20] |
| riserva  | 3        | 12143 |          -0.51 | [-0.64, -0.39] |
| riserva  | 4+       |  4560 |          -1.12 | [-1.33, -0.91] |
| titolare | 1 (b2b)  | 12368 |          +0.27 | [+0.16, +0.37] |
| titolare | 2        | 47753 |          +0.17 | [+0.12, +0.23] |
| titolare | 3        | 10937 |          +0.05 | [-0.07, +0.16] |
| titolare | 4+       |  4525 |          -0.36 | [-0.57, -0.16] |

### Partite saltate di fila prima di questa (rientro)

| gruppo   | fascia   |      n |   scarto medio | IC95           |
|:---------|:---------|-------:|---------------:|:---------------|
| tutti    | 0        | 144867 |          +0.16 | [+0.12, +0.20] |
| tutti    | 1-2      |  11740 |          -1.16 | [-1.30, -1.01] |
| tutti    | 3-9      |   4627 |          -1.62 | [-1.84, -1.41] |
| tutti    | 10+      |    944 |          -2.24 | [-2.72, -1.76] |

## 3. Tutti insieme (regressione sullo scarto, effetti parziali)

Coefficienti: minuti in piu' per ogni minuto di assenti / punto di differenza / evento.

|                                  |   coef |   IC basso |   IC alto |
|:---------------------------------|-------:|-----------:|----------:|
| Intercept                        | -2.687 |     -2.852 |    -2.522 |
| miss_min_same_pos:C(position)[C] | +0.093 |     +0.083 |    +0.104 |
| miss_min_same_pos:C(position)[F] | +0.055 |     +0.052 |    +0.058 |
| miss_min_same_pos:C(position)[G] | +0.058 |     +0.055 |    +0.061 |
| miss_other                       | +0.036 |     +0.034 |    +0.038 |
| diff:titolare                    | +0.029 |     +0.019 |    +0.039 |
| diff:I(1 - titolare)             | +0.011 |     +0.001 |    +0.022 |
| b2b:titolare                     | +0.006 |     -0.106 |    +0.117 |
| rientro                          | -1.962 |     -2.151 |    -1.774 |

R2 sullo scarto: 0.074
