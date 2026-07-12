@echo off
rem Claude Auto-Continue — start
cd /d "%~dp0"

rem install dependency if missing
py -3 -c "import uiautomation" 2>nul
if errorlevel 1 (
    echo installing dependency uiautomation...
    py -3 -m pip install --user uiautomation
)

rem launch without console window if possible (pyw); launch with console window if needed (py)
start "" pyw -3 claude_auto_continue.py
if errorlevel 1 py -3 claude_auto_continue.py
