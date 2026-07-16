@echo off
setlocal

if not exist ".\.venv\Scripts\activate.bat" (
    echo.
    echo [CHYBA] Virtualni prostredi .venv nenalezeno.
    echo Nejdriv spust setup.ps1 ^(pravym tlacitkem -^> Spustit pomoci PowerShell^).
    echo.
    pause
    exit /b 1
)

if not exist ".\.env" (
    echo.
    echo [UPOZORNENI] Soubor .env nenalezen.
    if exist ".\.env.example" (
        copy /Y ".\.env.example" ".\.env" >nul
        echo Vytvoren .env z .env.example - uprav si v nem BASIC_USER a BASIC_PASS.
    )
    echo.
)

call .\.venv\Scripts\activate.bat
python app.py

echo.
echo Aplikace byla ukoncena.
pause
