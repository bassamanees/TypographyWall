@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==== Typography Wall: building ONE-FILE app (release\TypographyWall.exe) ====
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail
set WALL_ONEFILE=1
%PY% -m PyInstaller wall_app.spec --noconfirm --clean --distpath release --workpath build\onefile
if errorlevel 1 goto fail
echo.
echo DONE.  Send this single file:  release\TypographyWall.exe
pause
exit /b 0
:fail
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
