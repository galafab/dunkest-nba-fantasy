# Punteggio Dunkest dalle statistiche (verificato sulla stagione 2025/26)

    base = PTS + DREB + 1,25·OREB + 1,5·AST + 1,5·STL − 1,5·TOV + 1,5·BLK − 0,5·BLKA
           − (FGA − FGM) − (FTA − FTM) + 1 (se titolare)
           + bonus doppie: +5 doppia doppia, +10 tripla, +50 quadrupla   (categorie ≥ 10 tra PTS, REB, AST, STL, BLK)
           + bonus triple segnate: +3 se 3PM = 3, +4 se 4, +5 se ≥ 5
    punti = base + 5% · |base|   se la squadra vince   (attenzione: valore assoluto, un −1 diventa −0,95)
    punti = base                  se perde

Verifica: ricalcolo esatto (±0,01) nel **99,78%** delle 27.706 partite 2025/26; i 62 scarti sono quasi tutti
del 16/11/2025 (probabili correzioni manuali del dato).

Le stagioni precedenti a volte usavano regole diverse: i loro PDK storici non vanno usati come riferimento,
ma i **box score** sì, ricalcolando il punteggio con la formula attuale.
