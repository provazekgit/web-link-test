# setup.ps1 - spustis pravym tlacitkem "Spustit pomoci PowerShell"
# nebo: powershell -ExecutionPolicy Bypass -File .\setup.ps1

Write-Host "== Web Test Hub - prvni instalace ==" -ForegroundColor Cyan

# 1) Overeni Pythonu - zjisti, ktery prikaz (py launcher, nebo primo python)
#    na tomto pocitaci skutecne funguje, a pouzij dal jen ten, ne oba napevno.
$pythonExe = $null
$pythonArgs = @()
try {
    py -3 --version | Out-Null
    $pythonExe = "py"
    $pythonArgs = @("-3")
} catch {
    try {
        python --version | Out-Null
        $pythonExe = "python"
        $pythonArgs = @()
    } catch {
        $pythonExe = $null
    }
}

if (-not $pythonExe) {
    Write-Host "[CHYBA] Python nebyl nalezen. Nainstaluj ho z https://www.python.org/downloads/ a spust znovu." -ForegroundColor Red
    Write-Host "Pri instalaci zaskrtni volbu 'Add python.exe to PATH'." -ForegroundColor Yellow
    Read-Host "Stiskni Enter pro ukonceni"
    exit 1
}

Write-Host "Pouzivam Python pres prikaz: $pythonExe $($pythonArgs -join ' ')" -ForegroundColor DarkGray

# 2) Vytvoreni virtualniho prostredi
Write-Host "Vytvarim .venv ..." -ForegroundColor Cyan
& $pythonExe @pythonArgs -m venv .venv
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
        Write-Host "[OK] Vytvoren .env z .env.example (aplikace pobezi rovnou bez hesla)." -ForegroundColor Green
    } else {
        Write-Host "[POZOR] Soubor .env.example chybi, vytvor .env rucne." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "[OK] Instalace dokoncena." -ForegroundColor Green
Write-Host "Aplikaci spustis prikazem run.bat (nebo: python app.py)" -ForegroundColor Cyan
Read-Host "Stiskni Enter pro ukonceni"
