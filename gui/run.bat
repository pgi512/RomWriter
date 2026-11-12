@echo off
cd /d "%~dp0"
python romwriter_gui.py
if errorlevel 1 pause
