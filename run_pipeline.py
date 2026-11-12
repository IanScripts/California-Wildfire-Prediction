
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

STEPS = [
    ("Download Open-Meteo weather", "open_meteo_api.py"),
    ("Fill missing weather dates", "open_meteo_fill_gap.py"),
    ("Merge weather datasets", "open_meteo_merge.py"),
    ("Prepare historical FW21 files", "open_meteo_prepare_history.py"),
    ("Run historical NFDRS4", "run_open_meteo_nfdrs.py"),
    ("Run forecast NFDRS4", "run_open_meteo_forecast.py"),
    ("Generate dashboard", "visualize_interactive_bi.py"),
]


def run_step(number, total, description, script):
    script_path = ROOT / script

    if not script_path.is_file():
        raise FileNotFoundError(
            f"Required script not found: {script_path}"
        )

    print()
    print("=" * 65, flush=True)
    print(f"STEP {number}/{total}: {description}", flush=True)
    print("=" * 65, flush=True)

    started = time.perf_counter()

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=ROOT,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"{script} failed with exit code {result.returncode}"
        )

    elapsed = time.perf_counter() - started
    print(f"Step completed in {elapsed:.2f} seconds", flush=True)


def main():
    start = time.perf_counter()

    print("=" * 65)
    print("SOUTHERN CALIFORNIA WILDFIRE AUTOMATION")
    print("=" * 65)
    print("Started:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    cli = ROOT / "build" / "bin" / "NFDRS4_cli.exe"
    init_file = ROOT / "data" / "NFDRSInitSample.txt"

    if not cli.is_file():
        raise FileNotFoundError(f"NFDRS4 executable missing: {cli}")

    if not init_file.is_file():
        raise FileNotFoundError(f"Initialization file missing: {init_file}")

    total = len(STEPS)

    for number, (description, script) in enumerate(STEPS, start=1):
        run_step(number, total, description, script)

    elapsed = time.perf_counter() - start

    print()
    print("=" * 65)
    print("AUTOMATION COMPLETED")
    print("=" * 65)
    print(f"Steps completed: {total}/{total}")
    print(f"Total runtime: {elapsed:.2f} seconds")
    print(f"Dashboard: {ROOT / 'nfdrs_dashboard.html'}")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError) as error:
        print(f"\nAUTOMATION FAILED: {error}", file=sys.stderr)
        sys.exit(1)
