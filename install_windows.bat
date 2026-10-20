@echo off
rem Typography Wall - double-click to download and install the latest version from GitHub.
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/bassamanees/TypographyWall/main/install_windows.ps1 | iex"
if errorlevel 1 pause
