# Dunkest: NBA fantasy projections and lineup optimization

Dunkest is an NBA fantasy-basketball game: you pick 10 players and a coach under a credit budget, and each player
scores fantasy points from his box score. This project projects every NBA player's Dunkest score for each upcoming
game and matchday, with an uncertainty interval (10/30/50/70/90% quantiles) rather than a single number. It
estimates the win probability between two lineups and builds the optimal lineup automatically under the game's
credit, role and team constraints. A local web app puts it all together. I built it to make my own lineup decisions
from calibrated projections instead of gut feel. It was also a chance to build a complete sports-modelling system,
from data collection to a model in production, extending the player-rating work from my MSc thesis.

## Demo

▶️ **Demo video (6 min, English captions):** [media/demo_dunkest_en.mp4](media/demo_dunkest_en.mp4) (open the file and press play, or download it)

## How it works

```
data sources -> player rating -> minutes (start of season -> in season) -> per-stat models
             -> Monte Carlo game simulation -> fantasy score + intervals -> lineup optimizer -> web app
```

1. **Data.** Public box scores and matchday data from dunkest.com since 2018 (`dunkest_sync.py`, an incremental,
   resumable sync). Player bio and draft data from Basketball Reference (`bref_anagrafica.py`: rate-limited and
   cached, with fuzzy name matching via `difflib`). Rosters, credits, injuries and calendar come from the Dunkest
   Fantasy app (private script, not published). Everything is stored in SQLite, and modelling datasets are built as
   parquet files. Every feature is computed only from games played before the target game. Fantasy points are
   always recomputed from box scores with the current scoring formula, because older seasons used different rules.
2. **Player rating** (`rating_giocatori.py`). Hollinger PER rebuilt from box scores, plus a cumulative
   value-over-replacement term and Basketball-Reference-style rate stats (USG%, AST%, TS%, ...), combined into
   category scores and an overall rating. The start-of-season rating is a recency- and minutes-weighted average of
   the last 3 seasons, shrunk towards a prior (by draft range for rookies).
3. **Minutes, start of season** (`minuti_avvio*.py`). Minutes in a team's first 10 games, predicted from the
   previous season and the new roster. The models compared are a naive baseline, linear regression (statsmodels) and
   LightGBM. Features cover player rating, team hierarchy by role, minutes vacated by departed players and type of
   absence. A structural "team budget" model splits 240 minutes across 5 size bands with Gaussian-kernel role
   affinities, and its parameters are fitted with `scipy.optimize.minimize`. In production, LightGBM uses the
   structural prediction as an extra feature. All models are evaluated with walk-forward validation by season.
4. **Minutes, in season** (`minuti_in_stagione.py`). After each game, the start-of-season estimate is updated by
   two LightGBM models: one for a player's first game and one for every later game. Features: recent minutes
   (last 1/3/5, exponential averages), starts, and the usual minutes of teammates who are out.
5. **Per-stat models** (`stat_dati.py`, `stat_modelli.py`, `stat_glmm.R`). Per-minute rates for 10 counting stats
   (2PA, 3PA, FTA, OREB, DREB, AST, STL, BLK, blocks against, TOV) and the 3 shooting percentages. Compared models:
   - a shrunk player-rate baseline;
   - GLMMs in R with `glmmTMB` (log-minutes offset, player and player-season random effects, Poisson/NB1/NB2
     family chosen by AIC; binomial logit for shooting percentages);
   - LightGBM with a Poisson objective and log-minutes offset (cross-entropy weighted by attempts for
     percentages).

   Features include opponent stats allowed to the player's position, team and opponent strength computed from
   current rosters (`forza_squadre.py`), and player bio data. A post-hoc calibration on team/opponent strength
   was also tested.
6. **Simulation and fantasy score** (`stima_partita.py`). A Monte Carlo simulation with 2,000 draws per player-game.
   Each draw combines:
   - minutes noise from historical actual/expected ratios;
   - a shared gamma "form" factor;
   - negative-binomial (Poisson-gamma) counts;
   - binomial makes;
   - a Bernoulli starter flag;
   - a win/loss outcome from a logistic model on roster strength and home court, drawn once per game so teammates
     share it.

   The Dunkest formula is applied to each draw, games are summed per matchday, and quantiles are stored.
7. **Lineup optimizer** (`compila_squadra.py`). An integer linear program with `scipy.optimize.milp` (HiGHS) and
   binary variables for roster, starter, sixth man and captain. Constraints: 4 G / 4 F / 2 C, credits minus the
   coach's price, at most 3 players per NBA team (coach included), the allowed starting formations, a 50% bench
   weight and a 1.5x captain. The objective is either expected score or expected credit gain. It can force players
   in or out and returns a top-10 of lineups that differ by at least 2 players.
8. **Web app** (`app/`). A FastAPI backend serves a vanilla JS front end. It offers side-by-side lineup comparison
   with win probability, a sortable player list with projections and intervals, a per-player projection view that
   re-simulates when you change the expected minutes, and the "automatic lineup" dialog. The app runs locally
   (optionally on the LAN for phone use).

## Results

All numbers come from the reports in `docs/` (generated by the scripts). MAE is in minutes per game (out of 240
team minutes).

| What | Result | Compared with | Source |
|---|---|---|---|
| Scoring formula reproduction | exact (±0.01) in 99.78% of 27,706 games, 2025/26 | n/a | `punteggio.md` |
| Credit-change formula (not published by Dunkest, reverse-engineered by grid search) | 74.7% exact, 99.3% within ±0.1, ~50,000 player-matchdays 2020/21–2024/25 | n/a | `plusvalenze.md` |
| Start-of-season minutes, MAE (2,143 player-seasons, walk-forward 2021/22–2025/26) | **4.23** (LightGBM + team budget, production) | 4.32 LightGBM with original features; 4.55 naive previous-season minutes | `minuti_avvio_quote.md`, `minuti_avvio.md` |
| Same, rookies only (297) | 5.57 | 6.13 naive (draft-range average) | `minuti_avvio_quote.md`, `minuti_avvio.md` |
| In-season minutes, MAE per game (92,747 player-games, test seasons 2022/23–2025/26) | **4.76** (LightGBM) | 6.58 frozen start-of-season estimate; 4.84 exponential average; 5.24 Bayesian shrinkage | `minuti_in_stagione.md` |
| Team strength from rosters vs past results (2025/26, fitted on the same season: measures information content) | win log-loss 0.597, accuracy 0.683 | 0.601 and 0.680 from past results | `forza_squadre.md` |
| Lineup optimizer | exact optimum in about 1 second | n/a | `da_fare.md` |

**Per-stat models.** Mean Poisson deviance (lower is better) on 2025/26, 27,613 player-games, with actual
minutes as exposure, so these numbers measure the per-minute rate models only. GLMMs were fitted only for the
three shot-attempt stats.

| Stat | Baseline | GLMM | LightGBM | LightGBM + calibration |
|---|---:|---:|---:|---:|
| 2PA | 1.255 | 1.205 | 1.092 | 1.094 |
| 3PA | 1.063 | 1.041 | 0.919 | 0.920 |
| FTA | 2.052 | 2.050 | 1.979 | 1.977 |
| AST | 1.190 | – | 1.130 | 1.130 |
| DREB | 1.124 | – | 1.105 | 1.105 |
| TOV | 1.088 | – | 1.055 | 1.056 |

LightGBM beats the baseline and the GLMM on every shot-attempt stat. The team-strength calibration made no material
difference, so production uses plain LightGBM. **Interval coverage:** the share of games whose actual value falls
inside the model's 10–90% interval, against the share the model itself expects. For count data the expected share
is not exactly 80%. Examples for LightGBM: 2PA 88.3% vs 88.7%, AST 91.0% vs 91.4%, DREB 89.7% vs 90.0%. FTA is the
weakest case at 88.4% vs 91.9%.

**Known limitations** (from the docs):
- After 40+ games, a simple exponential average of recent minutes is slightly better than LightGBM (4.74 vs 4.79).
- Players returning from a full missed season are underestimated, because there is no historical injury list.
- The lineup win probability currently treats players as independent normals. Using the correlated per-game
  simulations is on the to-do list.

## Tech stack

- **Python:** pandas, NumPy, LightGBM, statsmodels, SciPy (`milp`/HiGHS, `optimize.minimize`), pyarrow/parquet,
  sqlite3
- **R:** glmmTMB, arrow, data.table
- **App:** FastAPI, Uvicorn, Pydantic; plain HTML/CSS/JavaScript front end
- **Storage and orchestration:** SQLite; Windows batch files

## Repository structure

| Path | Contents |
|---|---|
| `scripts/dunkest_sync.py`, `bref_anagrafica.py` | Data collection (dunkest.com, Basketball Reference) |
| `scripts/stima_dati.py`, `stat_dati.py` | Leakage-free modelling datasets (parquet) |
| `scripts/rating_giocatori.py`, `forza_squadre.py` | Player rating; team strength from rosters |
| `scripts/analisi_minuti.py`, `minuti_avvio*.py`, `minuti_in_stagione.py` | Minutes analysis and models |
| `scripts/stat_modelli.py`, `stat_glmm.R`, `stima_modelli.py`, `verifica_avversario.py` | Per-stat models, GLMMs, experiments and checks |
| `scripts/stima_partita.py` | Monte Carlo simulation and fantasy-score projections |
| `scripts/compila_squadra.py` | Integer-programming lineup optimizer |
| `app/server.py`, `app/static/` | FastAPI backend and web front end |
| `aggiorna_stime.bat`, `avvia_app.bat`, `stima_statistiche_glmm.bat` | Run the projection chain, start the app, fit the GLMMs |
| `docs/` | Design notes and generated model reports |

## Notes

- **Data is not included.** The SQLite database, parquet files, trained models and cached web pages are excluded
  from the repository.
- **`dunkest_fantasy.py` is not published on purpose.** It reads my private Dunkest Fantasy account (rosters,
  credits, injuries, calendar), so the code here documents the method but the full pipeline cannot be rerun as is.
- The design notes and model reports in `docs/` are in Italian, as are code comments and the app interface.
- Dunkest is a third-party fantasy game. This is an independent personal project, not affiliated with or endorsed
  by Dunkest.

## Author

**Fabio Galante**, Data Scientist, Milan.
GitHub: [github.com/galafab](https://github.com/galafab) ·
MSc thesis: [github.com/galafab/Thesis](https://github.com/galafab/Thesis) (statistical model for NBA offseason
strategy)
