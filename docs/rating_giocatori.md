# Rating dei giocatori (ricostruito dai box score)

Generato da `scripts/rating_giocatori.py`. Versione semplificata del rating della tesi:
PER ricostruito con la formula di Hollinger; al posto di BPM/WS/VORP un valore sopra il rimpiazzo
cumulato, (PER - 11) x minuti per partita / 36. Niente difesa da tracking (tiri contestati, % avversari).

## Validazione del rating di avvio (rango dentro la squadra)

Spearman medio per squadra fra i minuti per partita della stagione S (giocatori con >= 20 partite)
e (a) rating di avvio calcolato solo sulle stagioni precedenti, (b) minuti per partita dell'anno prima.

|   stagione |   n |   rho_rating_avvio |   rho_prev_min |   corr_rating_min |
|-----------:|----:|-------------------:|---------------:|------------------:|
|          3 | 338 |              0.448 |          0.75  |             0.53  |
|          5 | 373 |              0.588 |          0.738 |             0.582 |
|          7 | 364 |              0.545 |          0.772 |             0.571 |
|          9 | 380 |              0.58  |          0.782 |             0.601 |
|         13 | 380 |              0.624 |          0.796 |             0.621 |
|         19 | 391 |              0.615 |          0.853 |             0.614 |
|         25 | 383 |              0.594 |          0.747 |             0.593 |

## Top 30 PlayerRating 2025/26 (>= 800 minuti)

| name                    |     mp |   per |   Scoring |   Playmaking |   Rebounding |   DifesaBox |   Impatto |   TempoUso |   Overall |   PlayerRating |
|:------------------------|-------:|------:|----------:|-------------:|-------------:|------------:|----------:|-----------:|----------:|---------------:|
| Nikola Jokic            | 2483.7 |  31.2 |      88.7 |         91.3 |         82.4 |        70.6 |      97.5 |       91.8 |      88   |           97.7 |
| Giannis Antetokounmpo   | 1039.2 |  32.6 |      92.7 |         88.1 |         80.7 |        69.1 |      96.4 |       91.7 |      87.6 |           96.8 |
| Shai Gilgeous-Alexander | 2804   |  29.5 |      91.2 |         90.1 |         64.8 |        71.2 |      94.2 |       92.6 |      87.9 |           95.5 |
| Luka Doncic             | 2288.6 |  28.1 |      90.3 |         89.8 |         71.5 |        70.4 |      93   |       96.7 |      88   |           94.9 |
| Victor Wembanyama       | 2616.1 |  28.6 |      87.8 |         80.9 |         81.5 |        84.3 |      91.8 |       89.2 |      86.8 |           93.2 |
| Kawhi Leonard           | 2125   |  27.5 |      89.3 |         84.1 |         70.9 |        72   |      91   |       91.6 |      85.6 |           91.9 |
| Joel Embiid             | 1433.8 |  24.2 |      87.2 |         83.5 |         73.7 |        69.1 |      86.4 |       91.6 |      83.5 |           87.5 |
| Tyrese Maxey            | 3113.8 |  21.1 |      84.1 |         87.5 |         64.1 |        70.9 |      83.7 |       93.3 |      83.9 |           85.9 |
| Jimmy Butler            | 1181.7 |  23.7 |      85.2 |         85.3 |         70.9 |        69   |      85.5 |       84.9 |      82   |           85.9 |
| Jaylen Brown            | 2691.7 |  21.6 |      85.8 |         83.4 |         70.4 |        67.4 |      83.5 |       94.6 |      82.9 |           85.2 |
| Stephen Curry           | 1344.3 |  22   |      89.3 |         84   |         64.8 |        67.8 |      83.3 |       90.3 |      82.9 |           85.1 |
| Donovan Mitchell        | 2994.5 |  21.4 |      86.8 |         84.6 |         66.3 |        68.2 |      83.2 |       91.8 |      82.9 |           85   |
| Jamal Murray            | 2890.1 |  21.2 |      85   |         87.6 |         65.3 |        65.8 |      83.2 |       90.8 |      82.5 |           84.7 |
| Kevin Durant            | 2880.8 |  21.3 |      86.5 |         80.8 |         66.9 |        68.2 |      83.5 |       90.8 |      82   |           84.6 |
| Cade Cunningham         | 2721.4 |  20.5 |      83   |         88.8 |         67.4 |        70.7 |      82.1 |       92.2 |      83.4 |           84.6 |
| Zion Williamson         | 1841.1 |  22.9 |      86.5 |         81.5 |         70.8 |        68.8 |      84.1 |       85.5 |      81.3 |           84.5 |
| Jalen Duren             | 2398.2 |  23.7 |      86.1 |         77.5 |         83.4 |        70.3 |      84.9 |       82.8 |      80.3 |           84.4 |
| Anthony Edwards         | 2426.6 |  20.8 |      86.8 |         81.5 |         66.5 |        69.7 |      82.5 |       92   |      82.5 |           84.2 |
| Alperen Sengun          | 2629.7 |  21.3 |      80.6 |         83.3 |         76.7 |        72.2 |      83   |       88.7 |      81.6 |           84   |
| Lauri Markkanen         | 1442.6 |  20.9 |      86.5 |         81.5 |         71.1 |        67.3 |      82.5 |       89.9 |      81.5 |           83.6 |
| Karl-Anthony Towns      | 2899.8 |  22.3 |      83.9 |         78.7 |         84.1 |        69.5 |      83.7 |       85.7 |      80.3 |           83.6 |
| Jalen Brunson           | 3291.4 |  20.2 |      84.5 |         88   |         63.5 |        64.4 |      81.7 |       92.4 |      82.2 |           83.6 |
| Jalen Johnson           | 2746   |  20.2 |      81.9 |         85.3 |         76.1 |        67.6 |      81.7 |       89.9 |      81.3 |           82.9 |
| LeBron James            | 2356.3 |  20.2 |      82.1 |         85.7 |         69.5 |        68.6 |      81.4 |       89.4 |      81.4 |           82.9 |
| Chet Holmgren           | 2456.4 |  21.3 |      84   |         77.7 |         77.4 |        75.9 |      82   |       82.6 |      80.4 |           82.5 |
| Deni Avdija             | 2378.9 |  19.9 |      84.9 |         84   |         70.6 |        66.6 |      81   |       90.1 |      81.3 |           82.5 |
| Devin Booker            | 2379.4 |  19.7 |      84.8 |         85   |         65.2 |        65   |      80.7 |       91.6 |      81.4 |           82.4 |
| Michael Porter Jr.      | 1688.6 |  19.9 |      85.2 |         82   |         72.7 |        66.4 |      80.7 |       90.5 |      81   |           82.1 |
| LaMelo Ball             | 2053.1 |  19.8 |      82.3 |         90.4 |         68.8 |        68.6 |      79.8 |       88.1 |      81.9 |           82.1 |
| Scottie Barnes          | 2921.8 |  19.5 |      80   |         82.9 |         72.8 |        75.2 |      80.4 |       87.2 |      81.1 |           82   |
