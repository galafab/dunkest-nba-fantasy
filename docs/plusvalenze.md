# Plusvalenze Dunkest (giocatori) — formula ricavata dai dati

Dunkest non pubblica la formula: il regolamento dice solo che dipende dal punteggio della giornata
e dal valore del giocatore. Questa è ricavata dai dati storici (ottobre 2026).

## Formula

    plusvalenza = arrotonda_0,1( 0,025 × (punti − 1,77 × crediti − 0,5) )

- limitata tra −1,5 e +1,5
- `punti` = punti Dunkest totali del giocatore nella giornata
- `crediti` = valore del giocatore **prima** della giornata
- i crediti non scendono mai sotto **4,0** (a 4,0 la minusvalenza non si applica)
- squadra NBA **a riposo** nella giornata: il giocatore prende il punteggio medio ma **nessuna** variazione
- squadra in campo ma giocatore **non entra**: **−0,1** fisso

In pratica: il punto di pareggio è circa `1,77 × crediti + 0,5` punti (10 crediti → ~18 punti),
e ogni ~4 punti sopra o sotto valgono ±0,1.

## Precisione

Su ~50.000 giornate-giocatore 2020/21–2024/25 (esclusi i giocatori scesi sotto 5 crediti):

- valore esatto: **74,7%**
- entro ±0,1: **99,3%**

## Metodo

- Dati: tabella statistiche dunkest.com per giornata (`dunkest_weeks`: punti e plusvalenza per giocatore).
- Il campo `cr` dell'API è il valore di **fine stagione**, uguale per tutte le giornate. Il valore prima di
  ogni giornata è ricostruito all'indietro: `cr_fine − Σ plusvalenze giornate successive + 0,1 × giornate
  saltate successive (squadra in campo)`. Le giornate di riposo della squadra non contano.
- Ricerca a griglia dei parametri `a × (punti − b × crediti − c)` massimizzando le previsioni esatte.

## Limiti e prossimi miglioramenti

- Il ~25% di scarti di 0,1 viene in parte dalla ricostruzione del prezzo (squadra del giocatore approssimata
  per i ceduti, giocatori usciti dal listone, prezzo interno forse con più decimali).
- Durante la stagione 2026/27 avremo i prezzi **reali** a ogni giornata (listone salvato da `aggiorna.bat`
  e storico partite fantasy con le colonne CR/PLUS): con quelli si può raffinare la formula.
- **Allenatori**: formula diversa, non ancora ricavata (servono i dati della stagione in corso).
