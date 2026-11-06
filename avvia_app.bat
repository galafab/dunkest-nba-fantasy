@echo off
rem Avvia l'app nel browser (solo su questo PC). Chiudi questa finestra per spegnerla.
title Dunkest - app
python "%~dp0app\server.py"
pause
