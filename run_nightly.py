"""
Unattended nightly run: scrape -> screen + tailor resumes -> morning summary (Q-Rattor/today.md).

    python run_nightly.py              full run
    python run_nightly.py --dry-run    scrape-free dry run: screens jobs already in jobs.jsonl, no model, no PDFs
    python run_nightly.py --no-scrape  only (re)generate resumes for jobs already scraped

Settings come from environment variables or an optional `nightly.env` file (KEY=VALUE per line, gitignored).
See nightly.env.example. Key points:

  JOOB_LLM_ORDER            default "ollama". Add "claude" ONLY for an account that is yours to automate
                            (a work/Team plan usually is not). Claude is also skipped automatically when the
                            5-hour or weekly usage in ~/.claude.json is above the thresholds below.
  JOOB_MAX_CLAUDE_CALLS     default 20 per night (each resume costs about 1-2 calls)
  JOOB_MAX_FIVE_HOUR_UTIL   default 60  (percent)   JOOB_MAX_WEEKLY_UTIL  default 60
  JOOB_JOB_LIMIT            default 25 resumes per night

If no LLM is available the resumes are still built, from your curated wording in resume_data.yaml.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QR = ROOT / "Q-Rattor"
LOGS = ROOT / "logs"
LOCK = LOGS / "nightly.lock"
LOGS.mkdir(exist_ok=True)


# ── settings ──────────────────────────────────────────────────────────────────────────────────────────
def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env_file(ROOT / "nightly.env")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("JOOB_LLM_ORDER", "ollama")
os.environ.setdefault("JOOB_MAX_CLAUDE_CALLS", "20")
os.environ.setdefault("JOOB_JOB_LIMIT", "25")
MAX_5H = float(os.environ.get("JOOB_MAX_FIVE_HOUR_UTIL", "60"))
MAX_7D = float(os.environ.get("JOOB_MAX_WEEKLY_UTIL", "60"))

LOG_FILE = LOGS / f"nightly-{date.today():%Y%m%d}.log"
notes: list[str] = []          # shown at the top of today.md


def log(msg: str) -> None:
    line = f"{datetime.now():%H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── lock: never two runs at once ──────────────────────────────────────────────────────────────────────
def acquire_lock() -> bool:
    if LOCK.exists():
        try:
            pid, started = LOCK.read_text().split(",")
            alive = subprocess.run(["kill", "-0", pid], capture_output=True).returncode == 0
            if alive and time.time() - float(started) < 3 * 3600:
                return False
        except Exception:
            pass                                     # unreadable/stale lock: take over
    LOCK.write_text(f"{os.getpid()},{time.time()}")
    return True


# ── LLM availability ──────────────────────────────────────────────────────────────────────────────────
def claude_usage_ok() -> tuple[bool, str]:
    """Use the usage snapshot Claude Code keeps in ~/.claude.json, but only if it is fresh."""
    try:
        d = json.loads((Path.home() / ".claude.json").read_text())["cachedUsageUtilization"]
        age_h = (time.time() * 1000 - d["fetchedAtMs"]) / 3.6e6
        u = d["utilization"]
        five, week = (u.get("five_hour") or {}).get("utilization"), (u.get("seven_day") or {}).get("utilization")
        if age_h > 3 or five is None or week is None:
            return True, f"usage snapshot is {age_h:.0f}h old, relying on the call cap"
        if five >= MAX_5H or week >= MAX_7D:
            return False, f"Claude usage too high (5h {five}%, week {week}%; limits {MAX_5H:.0f}/{MAX_7D:.0f})"
        return True, f"Claude usage ok (5h {five}%, week {week}%)"
    except Exception:
        return True, "no usage snapshot, relying on the call cap"


def ollama_ready() -> bool:
    if not shutil.which("ollama"):
        return False

    def up() -> bool:
        return subprocess.run(["curl", "-s", "-m", "2", "http://localhost:11434/api/tags"],
                              capture_output=True).returncode == 0

    if not up():
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(15):
            time.sleep(1)
            if up():
                break
        else:
            return False
    model = os.environ.get("JOOB_OLLAMA_MODEL", "qwen2.5:7b")
    out = subprocess.run(["ollama", "list"], capture_output=True, text=True).stdout
    return model.split(":")[0] in out                # never auto-pull a multi-GB model unattended


def settle_llm_order() -> None:
    order = [s.strip() for s in os.environ["JOOB_LLM_ORDER"].split(",") if s.strip()]
    if "claude" in order:
        ok, why = claude_usage_ok()
        log(why)
        if not ok:
            order.remove("claude")
            notes.append(why + "; Claude skipped tonight.")
    if "ollama" in order and not ollama_ready():
        order.remove("ollama")
        notes.append("Ollama is not running or its model is not installed; used curated wording instead.")
    os.environ["JOOB_LLM_ORDER"] = ",".join(order) if order else "none"
    log(f"LLM order tonight: {os.environ['JOOB_LLM_ORDER']}")


# ── steps ─────────────────────────────────────────────────────────────────────────────────────────────
def run_step(label: str, cmd: list[str], cwd: Path, timeout_s: int) -> tuple[bool, str]:
    log(f">> {label}")
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT: {label} after {timeout_s}s")
        notes.append(f"{label} timed out after {timeout_s // 60} min.")
        return False, ""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(p.stdout[-20000:] + p.stderr[-5000:])
    if p.returncode != 0:
        log(f"FAILED: {label} (exit {p.returncode})")
        notes.append(f"{label} failed (exit {p.returncode}); see {LOG_FILE.name}.")
        return False, p.stdout
    log(f"OK: {label}")
    return True, p.stdout


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_summary(before_skipped: dict, run_output: str) -> int:
    today = date.today().isoformat()
    entries = [e for e in load_json(QR / "final_apply.json", []) if e.get("generated_on") == today]
    skipped_now = load_json(QR / "output" / "skipped.json", {})
    new_skips = Counter(r for k, r in skipped_now.items() if k not in before_skipped)
    lines = [f"# joobScript: {datetime.now():%A %d %B %Y}", ""]
    lines += [f"- {n}" for n in notes] + ([""] if notes else [])
    lines.append(f"**{len(entries)} new resume(s) ready.** Everything (description, resume link, ATS keyword match) is in "
                 f"`Q-Rattor/jobs_to_apply.csv`: open each link, attach its PDF, then set `status`/`applied` there.")
    lines.append("")
    if entries:
        lines += ["| # | Role | Company | Where | Apply | Resume PDF | Made by |", "|---|---|---|---|---|---|---|"]
        for i, e in enumerate(sorted(entries, key=lambda x: -x.get("fit_score", 0)), 1):
            lines.append(f"| {i} | {e['job_name']} | {e['company']} | {e.get('location', '')} | "
                         f"[apply]({e['apply_link']}) | `Q-Rattor/{e['pdf_path']}` | {e.get('generator', '')} |")
        lines.append("")
    if new_skips:
        lines.append("Skipped tonight: " + "; ".join(f"{n}x {r}" for r, n in new_skips.most_common()))
    done = [l for l in run_output.splitlines() if l.startswith("Done.")]
    if done:
        lines.append("")
        lines.append(done[-1])
    (QR / "today.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(entries)


def notify(msg: str) -> None:
    if sys.platform == "darwin" and shutil.which("osascript"):
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "joobScript"'],
                       capture_output=True)


def prune_logs(days: int = 30) -> None:
    cutoff = time.time() - days * 86400
    for f in LOGS.glob("nightly-*.log"):
        if f.stat().st_mtime < cutoff:
            f.unlink()


def main() -> int:
    dry = "--dry-run" in sys.argv
    no_scrape = "--no-scrape" in sys.argv or dry
    if not acquire_lock():
        log("another nightly run is active; exiting")
        return 0
    try:
        log(f"=== nightly start{' (dry run)' if dry else ''} ===")
        prune_logs()
        py = sys.executable
        before_skipped = load_json(QR / "output" / "skipped.json", {})
        if not no_scrape:
            run_step("Scrape jobs", [py, "main.py"], ROOT, 30 * 60)   # a failed scrape still lets us use waiting jobs
        if dry:
            os.environ["JOOB_DRY_RUN"] = "1"
        else:
            settle_llm_order()
        ok, out = run_step("Generate resumes", [py, "pipeline.py"], QR, 90 * 60)
        n = write_summary(before_skipped, out) if not dry else 0
        log(f"=== nightly end: {n} resume(s) ===")
        if not dry:
            notify(f"{n} new resume(s) ready. See Q-Rattor/today.md")
        return 0 if ok else 1
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
