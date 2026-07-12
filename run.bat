@echo off
rem Claude Auto-Continue — start
cd /d "%~dp0"

rem doinstaluj zaleznosc, jesli brakuje
py -3 -c "import uiautomation" 2>nul
if errorlevel 1 (
    echo Instaluje pakiet uiautomation...
    py -3 -m pip install --user uiautomation
)

rem uruchom bez okna konsoli (pyw); awaryjnie z konsola (py)
start "" pyw -3 claude_auto_continue.py
if errorlevel 1 py -3 claude_auto_continue.py
