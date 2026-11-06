# Forza delle squadre dalle rose (generato da scripts/forza_squadre.py)

Test sul 2025/26 (adattamento sul test stesso: confronta solo quanta informazione contiene ogni misura).

| forza da                    |   R2 margine |   MAE margine |   log-loss vittoria |   accuratezza vittoria |
|:----------------------------|-------------:|--------------:|--------------------:|-----------------------:|
| risultati passati (attuale) |        0.238 |        12.282 |               0.601 |                  0.680 |
| rose (nuovo)                |        0.238 |        12.263 |               0.597 |                  0.683 |
| rose + risultati            |        0.251 |        12.197 |               0.595 |                  0.680 |

## Pesi (punti di margine per unita' di differenza nel profilo atteso)

|       |   peso |       t |
|:------|-------:|--------:|
| const | -2.035 | -12.637 |
| pts   |  0.073 |   2.192 |
| oreb  | -0.026 |  -0.453 |
| dreb  |  0.126 |   3.167 |
| ast   |  0.283 |   7.689 |
| stl   |  0.218 |   2.163 |
| blk   |  0.125 |   1.336 |
| tov   | -0.048 |  -0.607 |
| fga   | -0.031 |  -0.816 |
| fta   |  0.118 |   2.759 |
| tpm   |  0.152 |   2.587 |
| pm    |  0.169 |  28.560 |
| home  |  4.071 |  17.865 |

R2 in allenamento: 0.192
