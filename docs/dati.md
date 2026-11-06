# Dati: da dove arrivano e come si aggiornano

Tutto è nel database `data/dunkest.sqlite` (si apre in VS Code con l'estensione SQLite Viewer).

## Fonti

| Fonte | Cosa | Tabelle | Script |
|---|---|---|---|
| **dunkest.com** (pubblico) | partite e box score 2018–oggi, totali stagionali, punti e plusvalenze per giornata | `player_games`, `players`, `player_seasons`, `games`, `teams`, `dunkest_weeks` | `scripts/dunkest_sync.py` |
| **App Dunkest Fantasy** (serve il token in `token_dunkest.txt`) | listone (crediti, probabilità di giocare), infortuni, calendario, le tue squadre e classifiche | tabelle `fantasy_*` | `scripts/dunkest_fantasy.py` |
| **Basketball Reference** | altezza, peso, data di nascita, draft | `bref_players`, `bref_draft`, `players_bio` (collega i nostri id a Basketball Reference) | `scripts/bref_anagrafica.py` |

Calcolati da noi (non scaricati): `data/stima_dataset.parquet`, rigenerabile con `python scripts\stima_dati.py`.

## Aggiornamenti

**Durante la stagione, di solito: solo Dunkest.** Doppio clic su `aggiorna.bat`.

**Basketball Reference, poche volte l'anno:**

    python scripts\bref_anagrafica.py --refresh

- quando: **inizio stagione**, dopo il **draft** (giugno), e ogni tanto in stagione se arrivano giocatori nuovi
  (rookie non scelti al draft o firmati a stagione in corso);
- dura circa 2–3 minuti: riscarica solo l'indice giocatori e il draft dell'anno, i draft passati non cambiano;
- lancialo **dopo** `aggiorna.bat`, così abbina anche i giocatori appena entrati nel listone.

Senza `--refresh` non scarica niente di nuovo (usa le pagine salvate in `data/bref_cache`), ricalcola solo gli
abbinamenti: utile se è cambiato solo il listone.

**Stime dei punteggi (pagina, modalità "Stima"):** doppio clic su `aggiorna_stime.bat` dopo `aggiorna.bat`
(3-5 minuti). Calcola le prossime 3 giornate nelle tabelle `stima_giornata` (per giocatore e giornata) e
`stima_partita` (per partita). I modelli sono in `data/models`; per riallenarli:
`python scripts\stima_partita.py --riallena`.

## Da sapere

- Gli id dei giocatori sono diversi tra statistiche Dunkest e app Fantasy: il collegamento è
  `fantasy_players.stats_player_id`. Lo crea `dunkest_fantasy.py`, e `bref_anagrafica.py` completa quelli mancanti.
- I rookie compaiono nell'anagrafica di Basketball Reference dal draft (se scelti) o dalla prima partita giocata
  (se non scelti).
- I PDK delle stagioni passate seguivano regole diverse: per i punteggi si ricalcola tutto dai box score
  con la formula in `docs/punteggio.md`.
