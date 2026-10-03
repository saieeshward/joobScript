# One-shot setup for joobScript on Windows 10/11 (job scraper + Q-Rattor resume pipeline).
# Detects what's missing, installs it with winget, then verifies it all works.
#
#   powershell -ExecutionPolicy Bypass -File setup.ps1            install everything
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -NoLLM     skip Ollama + the 4.7GB model
#
# Safe to re-run - every step skips itself if already done.
param([switch]$NoLLM)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Model = "qwen2.5:7b"

function Say($m)  { Write-Host "`n==> $m" -ForegroundColor Blue }
function Ok($m)   { Write-Host "    [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    [!]  $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "    [x]  $m" -ForegroundColor Red; exit 1 }
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }

# Pick up PATH changes from installers without opening a new terminal
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Winget-Install($id) {
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

# -- 0. winget ----------------------------------------------------------------
Say "Checking winget"
if (-not (Have winget)) {
    Die "winget not found. Install 'App Installer' from the Microsoft Store, then re-run."
}
Ok "winget $(winget --version)"

# -- 1. git + Python 3.10+ ----------------------------------------------------
Say "Checking git and Python"
if (-not (Have git)) { Warn "git not found - installing"; Winget-Install "Git.Git" }
Ok (git --version)

function Find-Python {
    # The 'py' launcher is the reliable way on Windows; 'python' may be the Store stub
    foreach ($v in "3.13", "3.12", "3.11", "3.10") {
        if (Have py) {
            $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() }
        }
    }
    if (Have python) {
        $p = & python -c "import sys; print(sys.executable) if sys.version_info >= (3, 10) else sys.exit(1)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() }
    }
    return $null
}
$Py = Find-Python
if (-not $Py) {
    Warn "No Python 3.10+ found - installing Python 3.12"
    Winget-Install "Python.Python.3.12"
    $Py = Find-Python
    if (-not $Py) { Die "Python installed but not on PATH - open a new terminal and re-run." }
}
Ok "$(& $Py --version) at $Py"

# -- 2. Virtualenv + Python packages ------------------------------------------
Say "Creating .venv and installing Python packages"
$VPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VPy)) { & $Py -m venv .venv }
& $VPy -m pip install -q --upgrade pip
& $VPy -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { Die "pip install failed" }
Ok "Python packages installed"

Say "Installing headless Chromium for Playwright"
& $VPy -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Die "playwright install failed" }
Ok "Chromium ready"

# -- 3. LaTeX via MiKTeX (turns each resume into a PDF) -----------------------
Say "Checking LaTeX"
if (-not (Have pdflatex)) {
    Warn "pdflatex not found - installing MiKTeX (~200MB)"
    Winget-Install "MiKTeX.MiKTeX"
    $mk = Join-Path $env:LOCALAPPDATA "Programs\MiKTeX\miktex\bin\x64"
    if ((Test-Path $mk) -and ($env:Path -notlike "*$mk*")) { $env:Path += ";$mk" }
}
if (-not (Have pdflatex)) { Die "pdflatex still not on PATH - open a new terminal and re-run." }

# Let MiKTeX fetch missing packages silently instead of popping up dialogs mid-run
if (Have initexmf) { & initexmf --set-config-value="[MPM]AutoInstall=1" 2>$null | Out-Null }

# Every package resume_template.tex.j2 uses - install any that are missing
$missing = @()
foreach ($s in "geometry", "enumitem", "hyperref", "microtype", "titlesec", "parskip") {
    $found = & kpsewhich "$s.sty" 2>$null
    if (-not $found) { $missing += $s }
}
if ($missing.Count -gt 0) {
    Warn "Missing LaTeX packages: $($missing -join ', ') - installing"
    if (Have miktex) {
        & miktex packages update-package-database
        foreach ($s in $missing) { & miktex packages install $s }
    } elseif (Have mpm) {
        foreach ($s in $missing) { & mpm --install=$s }
    }
}
Ok "pdflatex + template packages present"

# -- 4. Ollama + model (local LLM that tailors each resume) -------------------
function Ollama-Up {
    try { Invoke-RestMethod http://localhost:11434/api/tags -TimeoutSec 2 | Out-Null; return $true }
    catch { return $false }
}

if (-not $NoLLM) {
    Say "Checking Ollama"
    if (-not (Have ollama)) {
        Warn "Ollama not found - installing"
        Winget-Install "Ollama.Ollama"
        $ol = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
        if ((Test-Path $ol) -and ($env:Path -notlike "*$ol*")) { $env:Path += ";$ol" }
    }
    if (-not (Have ollama)) { Die "Ollama installed but not on PATH - open a new terminal and re-run." }
    Ok "ollama found"

    if (-not (Ollama-Up)) {
        Warn "Ollama server not running - starting it"
        Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
        for ($i = 0; $i -lt 20 -and -not (Ollama-Up); $i++) { Start-Sleep 1 }
    }
    if (-not (Ollama-Up)) { Die "Couldn't reach Ollama on localhost:11434 - open the Ollama app, then re-run." }
    Ok "Ollama server running"

    $models = (& ollama list) -join "`n"
    if ($models -match [regex]::Escape($Model)) {
        Ok "$Model already downloaded"
    } else {
        Warn "Downloading $Model (~4.7GB, one-time)"
        & ollama pull $Model
    }
} else {
    Warn "Skipping Ollama (-NoLLM). Set USE_OLLAMA = False in Q-Rattor\pipeline.py to silence retries."
}

# -- 5. Folders + personal files ----------------------------------------------
New-Item -ItemType Directory -Force -Path logs, "Q-Rattor\output" | Out-Null
if (-not (Test-Path "Q-Rattor\resume_data.yaml")) {
    Copy-Item "Q-Rattor\resume_data.example.yaml" "Q-Rattor\resume_data.yaml"
    Warn "Created Q-Rattor\resume_data.yaml from the template - fill it in with YOUR resume."
}

# -- 6. Verify everything end to end ------------------------------------------
Say "Verifying install"
$check = @"
import importlib
for m in ("playwright", "playwright_stealth", "openpyxl", "yaml", "requests"):
    importlib.import_module(m)
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    p.chromium.launch(headless=True).close()
"@
$check | & $VPy -
if ($LASTEXITCODE -ne 0) { Die "Python/Chromium check failed" }
Ok "Python imports + headless Chromium launch"

$tmp = Join-Path $env:TEMP ("joob_tex_" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
@"
\documentclass{article}
\usepackage{geometry,enumitem,hyperref,microtype,titlesec,parskip}
\begin{document}ok\end{document}
"@ | Set-Content -Encoding ASCII (Join-Path $tmp "t.tex")
& pdflatex -interaction=nonstopmode -halt-on-error -output-directory $tmp (Join-Path $tmp "t.tex") | Out-Null
if ($LASTEXITCODE -ne 0) { Die "LaTeX test compile failed - see $tmp\t.log" }
Remove-Item -Recurse -Force $tmp
Ok "LaTeX compiles a test PDF"

if (-not $NoLLM) {
    $body = @{ model = $Model; stream = $false; messages = @(@{ role = "user"; content = "say ok" }) } | ConvertTo-Json -Depth 5
    try {
        Invoke-RestMethod http://localhost:11434/api/chat -Method Post -Body $body -ContentType "application/json" -TimeoutSec 300 | Out-Null
        Ok "$Model answers"
    } catch { Die "$Model didn't respond - try: ollama run $Model" }
}

# -- 7. Personalise the search (roles + countries) ---------------------------
if (-not (Test-Path "user_config.py")) {
    Say "Personalising your job search"
    & $VPy personalize.py
}

# -- Done ----------------------------------------------------------------------
Say "All set. Before your first run, personalise these:"
Write-Host @"
    1. Q-Rattor\resume_data.yaml       -> your resume (the source of truth)
    2. Q-Rattor\job_profiles.yaml      -> priority_* names + summary_focus for YOU
    3. Q-Rattor\resume_template.tex.j2 -> change the hardcoded "Dublin, Ireland"
    4. Search roles/countries          -> re-run any time: .venv\Scripts\python.exe personalize.py

    Then run:   .venv\Scripts\python.exe run_daily.py
    Full guide: HANDOFF.md
"@
