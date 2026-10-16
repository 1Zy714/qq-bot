@echo off
title Strata IQ2_XS
cd /d "Y:\Strata"
"Y:\Strata\.venv\Scripts\python.exe" "Y:\Strata\serve\server.py" "--engine" "strata" "--config" "Y:\Strata\strata-iq2_xs.json" "--port" "8080" "--open"
if errorlevel 1 pause
