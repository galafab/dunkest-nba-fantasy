@echo off
rem Ricalcola le stime dei punteggi per le prossime 3 giornate (circa 3-5 minuti).
rem Lancialo dopo aggiorna.bat: usa rose, infortuni e calendario appena scaricati.
rem 1) rating dei giocatori, minuti di inizio stagione (congelati dalla prima partita) e minuti
rem    aggiornati con le partite giocate e gli assenti
rem 2) simulazione delle partite e punteggio Dunkest (media e range) -> pagina "Stima"
title Dunkest - stime punteggi
python "%~dp0scripts\rating_giocatori.py"
python "%~dp0scripts\minuti_avvio_quote.py" --prevedi
python "%~dp0scripts\minuti_in_stagione.py" --prevedi
python "%~dp0scripts\stima_partita.py"
echo.
pause
