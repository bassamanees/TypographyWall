@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==== Typography Wall: building the app ====
rem Python المشروع (.venv) إن وُجد، وإلا Python المثبّت على الجهاز
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail
%PY% -m PyInstaller wall_app.spec --noconfirm --clean
if errorlevel 1 goto fail
echo.
echo Creating desktop shortcut...
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Typography Wall.lnk'); $s.TargetPath='%~dp0dist\TypographyWall\TypographyWall.exe'; $s.WorkingDirectory='%~dp0dist\TypographyWall'; $s.IconLocation='%~dp0app.ico'; $s.Save()"
echo.
echo DONE.  App folder: dist\TypographyWall   (double-click TypographyWall.exe or the desktop shortcut)
echo To share it, copy the whole TypographyWall folder (or zip it).
pause
exit /b 0
:fail
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
