@echo off
rem Double-click: pull main, merge Claude's cloud branches (asks first), push, install into Blender.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py update.py) else (python update.py)
