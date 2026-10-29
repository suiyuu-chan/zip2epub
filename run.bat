@echo off
rem zip2epub launcher (ASCII only: cmd reads .bat in the OEM code page)
setlocal
cd /d "%~dp0"

set "BUNDLED=C:\Users\%USERNAME%\.dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe"
if not exist "%BUNDLED%" goto try_py
"%BUNDLED%" -c "import PIL, lxml, tkinter" >nul 2>nul
if errorlevel 1 goto try_py
start "" "%BUNDLED%" -m z2e.cli gui
exit /b 0

:try_py
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -c "import PIL, lxml, tkinter" >nul 2>nul
if errorlevel 1 goto try_python
start "" py -3 -m z2e.cli gui
exit /b 0

:try_python
where python >nul 2>nul
if errorlevel 1 goto nodeps
python -c "import PIL, lxml, tkinter" >nul 2>nul
if errorlevel 1 goto nodeps
start "" python -m z2e.cli gui
exit /b 0

:nodeps
echo [zip2epub] Missing dependencies: Pillow / lxml / tkinter
echo     pip install -r requirements.txt
pause
exit /b 1
