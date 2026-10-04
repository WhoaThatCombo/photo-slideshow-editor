@echo off
title Photo Slideshow Editor
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo Python was not found on this computer.
  echo Install it from https://python.org  ^(tick "Add Python to PATH"^)
  echo.
  pause
  exit /b 1
)

echo Starting the Photo Slideshow Editor...
echo A browser window will open in a moment.
echo.
echo Leave this black window open while you work.
echo Close it when you are finished.
echo.
python "_photo_editor.py" %*

if errorlevel 1 (
  echo.
  echo The editor stopped unexpectedly. The message above says why.
  pause
)
