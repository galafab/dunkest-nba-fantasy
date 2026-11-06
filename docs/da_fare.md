# Cose da fare

## FATTO (8/10/2026): modello dei minuti

Catena in `aggiorna_stime.bat`: rating -> minuti di inizio stagione -> minuti in stagione -> simulazione.

- **Rating** (`scripts/rating_giocatori.py`, `docs/rating_giocatori.md`): stile tesi, dai box score
  (PER ricostruito, valore sopra il rimpiazzo, minuti e USG%); rating di avvio dalle ultime 3 stagioni.
- **Inizio stagione** (`scripts/minuti_avvio_quote.py`, tabella `stima_minuti_avvio`): LightGBM con rating,
  gerarchia di squadra, minuti liberati, tipo di assenza e bilancio di squadra per fasce di taglia.
  Errore 4,23 minuti (prima 4,32). Congelata dalla prima partita giocata (`--forza` per rigenerarla);
  la versione precedente resta in `stima_minuti_avvio_vecchio`.
- **In stagione** (`scripts/minuti_in_stagione.py`, tabella `stima_minuti_stagione`, usata da `stima_partita.py`):
  un modello per la prima partita e uno per le successive (minuti recenti, partenze, compagni assenti).
  Errore per partita 4,76 contro 6,58 della stima ferma.
- Limiti noti: alla prima partita chi ha 20-30 minuti è sottostimato di circa 0,8; oltre le 40 partite la media
  recente fa leggermente meglio del LightGBM (4,74 contro 4,79); chi salta una stagione intera (Haliburton)
  resta stimato al ribasso perché manca una lista infortuni storica.
- Sharpe: non è "unico centro" per il modello (BKN ha anche Randle, Wolf e Wagner, F-C); stima 19,6 a inizio stagione.

## Compilazione automatica della squadra

- FATTO (8/10/2026), primo passo: pulsante **Compila** nella pagina Confronto (`scripts/compila_squadra.py`,
  `POST /api/autofill`). Scelto l'allenatore, compila rosa, quintetto, sesto uomo e capitano con il punteggio
  atteso più alto nella giornata selezionata (una sola giornata). Programmazione lineare intera (scipy, HiGHS):
  ottimo esatto in circa 1 secondo. Vincoli: crediti meno il prezzo dell'allenatore, 4 G / 4 F / 2 C,
  massimo 3 per squadra NBA allenatore compreso, moduli ammessi, panchina al 50%, capitano ×1,5.
  Valore di un giocatore = probabilità di giocare × stima media (0 se la squadra riposa).
- FATTO (8/10/2026): pulsante rinominato "Squadra automatica"; apre una finestra (niente avvisi del browser) con
  allenatore obbligatorio, giocatori da avere e giocatori da escludere (`include`/`exclude` nel solver).
  Campo con modulo fisso (selettore 2-2-1, 1-2-2, 2-1-2, 1-3-1, 3-1-1): rimuovere un giocatore svuota solo la sua maglia.
- FATTO (8/10/2026): obiettivo selezionabile (punteggio atteso / plusvalenza attesa in crediti, il punteggio fa da
  spareggio) e "Le prime 10": finestra con le squadre in classifica (#1-#10), campo a sinistra e nomi per ruolo a
  destra, pulsante "Usa questa squadra". Le squadre della classifica differiscono per almeno 2 giocatori
  (`MIN_DIFF` in `scripts/compila_squadra.py`). Plusvalenza attesa = formula di docs/plusvalenze.md sul punteggio
  medio, pesata con la probabilità di giocare (-0,1 se non gioca; squadra a riposo 0).
- Più avanti: tenere conto della squadra attuale e del costo dei cambi (-5 punti a cambio).

## Elenco giocatori per stima

- FATTO (8/10/2026): componente unico `playerList` (app.js), tabella ordinabile per colonna con ricerca, ruolo, squadra NBA,
  "solo chi gioca", "senza infortunati" e (nella scelta) "entro i crediti". Colonne: giocatore, partita, crediti, minuti,
  stima, intervallo 30-70%, probabilità di giocare, plusvalenza attesa, media, ultime 5. Usato dalla scheda
  "Elenco giocatori" (clic = scheda giocatore) e dalla scelta di uno slot sul campo (clic = inserisce); sostituisce
  anche la lista di "Squadra automatica". Mostra al massimo 300 righe. Su telefono, nella scelta, nasconde minuti,
  intervallo, media e ultime 5. Ordinamento iniziale: stima decrescente (prima era crediti).

Specifiche originali:

- Pagina o finestra con tutti i giocatori della giornata e la loro stima (media e intervallo), consultabile,
  ordinabile per qualsiasi colonna e filtrabile (ruolo, squadra NBA, crediti, probabilità di giocare, solo chi gioca,
  infortunati, ricerca per nome). Colonne probabili: nome, ruolo, squadra, avversario, crediti, minuti stimati,
  stima media, intervallo 30-70%, probabilità di giocare, plusvalenza attesa, media ultime 5.
- Deve poter essere richiamata anche al momento della scelta dei giocatori sul campo (richiesta 8/10/2026).
  Idea: un solo componente elenco, con due modi. **Consultazione** (scheda "Elenco giocatori" accanto a "Confronto"):
  tutti i ruoli, clic sul nome = scheda del giocatore. **Scelta** (da uno slot vuoto o "Cambia giocatore"): stesso
  elenco ma con ruoli e crediti già limitati dallo slot, clic sulla riga = inserisce il giocatore.
  Oggi `openPicker` (app.js) è già una lista con ricerca, ruoli e ordinamento: va sostituita dal nuovo elenco a
  colonne (ordinabile per ogni colonna), non affiancata, così c'è una sola lista da mantenere.

## Da decidere (pagina confronto)

- Range della squadra: oggi è la somma dei 10% e dei 90% dei giocatori (troppo largo); usare il 10-90% simulato.
- Probabilità di vittoria: oggi giocatori indipendenti e normali; usare le simulazioni per partita (con correlazioni).
- FATTO (8/10/2026): tolto il pulsante "Riferimento"; parquet più chiaro; sul campo il capitano mostra il punteggio
  già ×1,5; nella finestra della stima, con più partite in giornata, il totale della giornata come somma delle partite.

## Più avanti

- Pulsante **Salva** (oggi salva in `/api/saved-teams` ma senza modo di riaprire: "Carica" è stato tolto l'8/10/2026): deve invece
  modificare direttamente la squadra dell'utente su Dunkest. Per ora lasciato com'è.

- Creare un video demo che mostri tutte le funzionalità dell'app.
