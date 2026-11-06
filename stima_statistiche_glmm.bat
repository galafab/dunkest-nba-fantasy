@echo off
rem GLMM delle singole statistiche (R + glmmTMB), circa 30-60 minuti.
rem Avanzamento in data\stat_glmm.log. Si puo' chiudere la finestra a fine lavoro.
title Dunkest - GLMM statistiche
"%LOCALAPPDATA%\Programs\R\R-4.6.1\bin\Rscript.exe" "%~dp0scripts\stat_glmm.R"
echo.
pause
