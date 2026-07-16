# setup.ps1 - spustis pravym tlacitkem "Spustit pomoci PowerShell"
# nebo: powershell -ExecutionPolicy Bypass -File .\setup.ps1

Write-Host "== Web Test Hub - prvni instalace ==" -ForegroundColor Cyan

# 1) Overeni Pythonu
$pythonOk = $false
try {
    py -3 --version
    $pythonOk = $true
} catch {
    try {
        python --version
        $pythonOk = $true
    } catch {
        $pythonOk = $false
    }
}

if (-not $pythonOk) {
    Write-Host "[CHYBA] Python nebyl nalezen. Nainstaluj ho z https://www.python.org/downloads/ a spust znovu." -ForegroundColor Red
    Read-Host "Stiskni Enter pro ukonceni"
    exit 1
}

# 2) Vytvoreni virtualniho prostredi
Write-Host "Vytvarim .venv ..." -ForegroundColor Cyan
py -3 -m venv .venv
if (-not (Test-Path ".\.venv\Scripts\activate.ps1")) {
    Write-Host "[CHYBA] Nelze aktivovat .venv - zkontroluj opravneni." -ForegroundColor Red
    Read-Host "Stiskni Enter pro ukonceni"
    exit 1
}

# 3) Aktivace a instalace balicku
Write-Host "Instaluji balicky..." -ForegroundColor Cyan
. .\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt

# 4) Instalace Playwright prohlizecu
Write-Host "Instaluji Playwright prohlizece..." -ForegroundColor Cyan
python -m playwright install

# 5) Kopie .env souboru
if (-not (Test-Path ".\.env")) {
    if (Test-Path ".\.env.example") {
        Copy-Item ".\.env.example" ".\.env"
        Write-Host "[OK] Vytvoren .env z .env.example - dopln si v nem BASIC_USER a BASIC_PASS." -ForegroundColor Green
    } else {
        Write-Host "[POZOR] Soubor .env.example chybi, vytvor .env rucne." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "[OK] Instalace dokoncena." -ForegroundColor Green
Write-Host "Aplikaci spustis prikazem run.bat (nebo: python app.py)" -ForegroundColor Cyan
Read-Host "Stiskni Enter pro ukonceni"
