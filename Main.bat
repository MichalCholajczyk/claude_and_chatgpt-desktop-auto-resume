@echo off
rem Auto-Resume — pick ChatGPT, Claude or both
cd /d "%~dp0"

rem install dependency if missing
py -3 -c "import uiautomation" 2>nul
if errorlevel 1 (
    echo installing dependency uiautomation...
    py -3 -m pip install --user uiautomation
)

rem launch the picker without console window if possible (pyw); with console window if needed (py)
start "" pyw -3 launcher.py
if errorlevel 1 py -3 launcher.py
