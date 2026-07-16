# setup.ps1  — spustíš pravým tlačítkem "Spustit pomocí PowerShell" 
# nebo: powershell -ExecutionPolicy Bypass -File .\setup.ps1

Write-Host "== Web Test Hub – první instalace ==" -ForegroundColor Cyan

# 1️⃣ Ověření Pythonu
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
    Write-Host "❌ Python nebyl nalezen. Nainstaluj ho z https://www.python.org/downloads/ a spusť znovu." -ForegroundColor Red
    Read-Host "Stiskni Enter pro ukončení"
    exit 1
}

# 2️⃣ Vytvoření virtuálního prostředí
Write-Host "Vytvářím .venv ..." -ForegroundColor Cyan
py -3 -m venv .venv
if (-not (Test-Path ".\.venv\Scripts\activate.ps1")) {
    Write-Host "❌ Nelze aktivovat .venv – zkontroluj oprávnění." -ForegroundColor Red
    exit 1
}

# 3️⃣ Aktivace a instalace balíčků
Write-Host "Instaluji balíčky..." -ForegroundColor Cyan
. .\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt

# 4️⃣ Instalace Playwright prohlížečů
Write-Host "Instaluji Playwright prohlížeče..." -ForegroundColor Cyan
python -m playwright install

# 5️⃣ Kopie .env souboru
if (-not (Test-Path ".\.env")) {
    if (Test-Path ".\.env.example") {
        Copy-Item ".\.env.example" ".\.env"
        Write-Host "✅ Vytvořen .env z .env.example" -ForegroundColor Green
    } else {
        Write-Host "⚠️ Soubor .env.example chybí, vytvoř ho ručně." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "✅ Instalace dokončena." -ForegroundColor Green
Write-Host "Aplikaci spustíš příkazem: python app.py" -ForegroundColor Cyan
Write-Host "nebo jednoduše přes run.bat (až ho vytvoříme)" -ForegroundColor Yellow
