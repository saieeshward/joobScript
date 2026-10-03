#!/usr/bin/env bash
# One-shot setup for joobScript (job scraper + Q-Rattor resume pipeline).
# Detects what's missing on this machine, installs it, then verifies it all works.
#
#   ./setup.sh            install everything
#   ./setup.sh --no-llm   skip Ollama + the 4.7GB model (resumes use fallback bullets)
#
# Supports macOS (Homebrew) and Debian/Ubuntu (apt). On Windows, run inside WSL (Ubuntu).
# Safe to re-run — every step skips itself if already done.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

MODEL="qwen2.5:7b"
WITH_LLM=1
[[ "${1:-}" == "--no-llm" ]] && WITH_LLM=0

say()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '    \033[31m✗\033[0m %s\n' "$*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

OS="$(uname)"
if [[ "$OS" == "Darwin" ]]; then
    PLATFORM=mac
elif [[ "$OS" == "Linux" ]] && have apt-get; then
    PLATFORM=apt
else
    die "Unsupported system ($OS). Use macOS, or Ubuntu/Debian (WSL works on Windows)."
fi

apt_install() { sudo apt-get install -y "$@"; }

# ── 0. Package manager ───────────────────────────────────────────────────────
if [[ $PLATFORM == mac ]]; then
    say "Checking Homebrew"
    if ! have brew; then
        warn "Homebrew not found — installing (will ask for your password)"
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Put brew on PATH for the rest of this script (Apple Silicon vs Intel)
        [[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
        [[ -x /usr/local/bin/brew ]]    && eval "$(/usr/local/bin/brew shellenv)"
    fi
    ok "$(brew --version | head -1)"
else
    say "Updating apt package lists"
    sudo apt-get update -y
fi

# ── 1. git, curl, Python 3.10+ ───────────────────────────────────────────────
say "Checking git, curl and Python"
if [[ $PLATFORM == mac ]]; then
    have git  || brew install git
    have curl || brew install curl
else
    have git  || apt_install git
    have curl || apt_install curl
fi

py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
    if have "$cand" && py_ok "$(command -v "$cand")"; then PY="$(command -v "$cand")"; break; fi
done
if [[ -z "$PY" ]]; then
    warn "No Python 3.10+ found — installing"
    if [[ $PLATFORM == mac ]]; then
        brew install python@3.12
        PY="$(brew --prefix python@3.12)/bin/python3.12"
    else
        apt_install python3 python3-venv python3-pip
        PY="$(command -v python3)"
        py_ok "$PY" || die "apt's python3 is older than 3.10 — upgrade your distro or install Python 3.10+ manually."
    fi
fi
# Debian/Ubuntu ship python without venv support by default
if [[ $PLATFORM == apt ]] && ! "$PY" -c 'import ensurepip' 2>/dev/null; then
    apt_install "python$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')-venv" \
        || apt_install python3-venv
fi
ok "git, curl, $("$PY" --version)"

# ── 2. Virtualenv + Python packages ──────────────────────────────────────────
say "Creating .venv and installing Python packages"
[[ -x .venv/bin/python ]] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
ok "Python packages installed"

say "Installing headless Chromium for Playwright"
if [[ $PLATFORM == apt ]]; then
    # --with-deps pulls the Linux system libraries Chromium needs (asks for sudo)
    .venv/bin/python -m playwright install --with-deps chromium
else
    .venv/bin/python -m playwright install chromium
fi
ok "Chromium ready"

# ── 3. LaTeX (turns each resume into a PDF) ──────────────────────────────────
say "Checking LaTeX"
if [[ $PLATFORM == mac ]]; then
    [[ -d /Library/TeX/texbin ]] && export PATH="/Library/TeX/texbin:$PATH"
    if ! have pdflatex; then
        warn "pdflatex not found — installing BasicTeX (~100MB, asks for your password)"
        brew install --cask basictex
        export PATH="/Library/TeX/texbin:$PATH"
    fi
else
    have pdflatex || apt_install texlive-latex-base texlive-latex-recommended texlive-latex-extra
fi
have pdflatex || die "pdflatex still not on PATH — open a new terminal and re-run ./setup.sh"

# Every package resume_template.tex.j2 uses — install any that are missing
missing=()
for sty in geometry enumitem hyperref microtype titlesec parskip; do
    kpsewhich "$sty.sty" >/dev/null || missing+=("$sty")
done
if (( ${#missing[@]} )); then
    warn "Missing LaTeX packages: ${missing[*]} — installing"
    if [[ $PLATFORM == mac ]]; then
        sudo tlmgr update --self
        sudo tlmgr install "${missing[@]}"
    else
        apt_install texlive-latex-recommended texlive-latex-extra
    fi
fi
ok "pdflatex + all template packages present"

# ── 4. Ollama + model (local LLM that tailors each resume) ───────────────────
ollama_up() { curl -sf http://localhost:11434/api/tags >/dev/null; }

if (( WITH_LLM )); then
    say "Checking Ollama"
    if ! have ollama; then
        warn "Ollama not found — installing"
        if [[ $PLATFORM == mac ]]; then
            brew install ollama
        else
            curl -fsSL https://ollama.com/install.sh | sh
        fi
    fi
    ok "$(ollama --version 2>/dev/null | head -1)"

    if ! ollama_up; then
        warn "Ollama server not running — starting it"
        if [[ $PLATFORM == mac ]] && brew list ollama >/dev/null 2>&1; then
            brew services start ollama   # also starts on every login
        elif have systemctl && systemctl list-unit-files ollama.service >/dev/null 2>&1; then
            sudo systemctl enable --now ollama
        else
            nohup ollama serve > logs/ollama.log 2>&1 &
        fi
        for _ in $(seq 1 20); do ollama_up && break; sleep 1; done
    fi
    ollama_up || die "Couldn't reach Ollama on localhost:11434 — open the Ollama app or run 'ollama serve', then re-run."
    ok "Ollama server running"

    if ollama list | awk '{print $1}' | grep -qx "$MODEL"; then
        ok "$MODEL already downloaded"
    else
        warn "Downloading $MODEL (~4.7GB, one-time)"
        ollama pull "$MODEL"
    fi
else
    warn "Skipping Ollama (--no-llm). Set USE_OLLAMA = False in Q-Rattor/pipeline.py to silence retries."
fi

# ── 5. Folders + personal files ──────────────────────────────────────────────
mkdir -p logs Q-Rattor/output
if [[ ! -f Q-Rattor/resume_data.yaml ]]; then
    cp Q-Rattor/resume_data.example.yaml Q-Rattor/resume_data.yaml
    warn "Created Q-Rattor/resume_data.yaml from the template — fill it in with YOUR resume."
fi

# ── 6. Verify everything end to end ──────────────────────────────────────────
say "Verifying install"
.venv/bin/python - <<'PY'
import importlib
for m in ("playwright", "playwright_stealth", "openpyxl", "yaml", "requests"):
    importlib.import_module(m)
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    p.chromium.launch(headless=True).close()
print("    \033[32m✓\033[0m Python imports + headless Chromium launch")
PY

TMP="$(mktemp -d)"
cat > "$TMP/t.tex" <<'TEX'
\documentclass{article}
\usepackage{geometry,enumitem,hyperref,microtype,titlesec,parskip}
\begin{document}ok\end{document}
TEX
if pdflatex -interaction=nonstopmode -halt-on-error -output-directory "$TMP" "$TMP/t.tex" >/dev/null 2>&1; then
    ok "LaTeX compiles a test PDF"
else
    die "LaTeX test compile failed — see $TMP/t.log"
fi
rm -rf "$TMP"

if (( WITH_LLM )); then
    curl -sf http://localhost:11434/api/chat \
        -d "{\"model\":\"$MODEL\",\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":\"say ok\"}]}" \
        >/dev/null && ok "$MODEL answers" || die "$MODEL didn't respond — try: ollama run $MODEL"
fi

# ── 7. Personalise the search (roles + countries) ───────────────────────────
if [[ -t 0 && ! -f user_config.py ]]; then
    say "Personalising your job search"
    .venv/bin/python personalize.py
fi

# ── Done ──────────────────────────────────────────────────────────────────────
say "All set. Before your first run, personalise these:"
cat <<EOF
    1. Q-Rattor/resume_data.yaml       → your resume (the source of truth)
    2. Q-Rattor/job_profiles.yaml      → priority_* names + summary_focus for YOU
    3. Q-Rattor/resume_template.tex.j2 → change the hardcoded "Dublin, Ireland"
    4. Search roles/countries          → re-run any time: .venv/bin/python personalize.py

    Then run:   .venv/bin/python run_daily.py
    Full guide: HANDOFF.md
EOF
