@echo off
title MultiCamProject auto deploy
rem Double-click and leave open: pulls main when GitHub has new commits and reloads the add-on in Blender.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py auto_deploy.py) else (python auto_deploy.py)
pause
