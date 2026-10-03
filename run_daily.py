"""
Daily pipeline runner.
    python run_daily.py
    or via cron: 0 2 * * * cd /path/to/joobScript && python run_daily.py >> logs/daily.log 2>&1
"""
import os, subprocess, sys, logging
from pathlib import Path

ROOT = Path(__file__).parent
(ROOT / "logs").mkdir(exist_ok=True)

# UTF-8 for child scripts so non-ASCII job titles don't crash on Windows log redirects
os.environ.setdefault("PYTHONUTF8", "1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "logs" / "daily.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("daily")

def run(label: str, cmd: list[str], cwd: Path = ROOT) -> bool:
    log.info(">> %s", label)
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        log.error("FAILED: %s (exit %d)", label, result.returncode)
        return False
    log.info("OK: %s", label)
    return True

def main():
    if not run("Scrape jobs", [sys.executable, "main.py"]):
        sys.exit(1)

    run("Sort by location", [sys.executable, "resort_jobs.py"])

    if not run("Generate resumes", [sys.executable, "pipeline.py"], cwd=ROOT / "Q-Rattor"):
        sys.exit(1)

    log.info("Daily run complete.")

if __name__ == "__main__":
    main()
