"""Run the additional California-wide data-source collectors.

Examples:
  python run_california_sources.py --catalog-only
  python run_california_sources.py --start-date 2000-01-01 --end-date 2026-11-05
  python run_california_sources.py --start-date 2024-01-01 --max-stations 10 --with-hrrr

This is deliberately separate from run_pipeline.py: the new statewide source
collectors do not alter or overwrite the existing six-location NFDRS4 workflow.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(script, args):
    command = [sys.executable, str(ROOT / script), *args]
    print("\n" + "=" * 68)
    print("RUNNING:", " ".join(command))
    print("=" * 68, flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-only", action="store_true")
    parser.add_argument("--start-date", default="2000-01-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--max-stations", type=int, default=None)
    parser.add_argument("--forecast-days", type=int, default=16)
    parser.add_argument("--with-hrrr", action="store_true",
                        help="Also retrieve NOAA HRRR GRIB fields; requires Herbie dependencies.")
    parser.add_argument("--hrrr-hours", nargs="+", type=int,
                        default=list(range(0, 19)))
    args = parser.parse_args()

    # Always refresh station metadata before any station-based weather request.
    run("iem_california.py", ["--catalog-only"])
    if args.catalog_only:
        print("\nStation catalog refreshed. No weather observations downloaded.")
        return

    iem_args = ["--start-date", args.start_date]
    if args.end_date:
        iem_args += ["--end-date", args.end_date]
    if args.max_stations:
        iem_args += ["--max-stations", str(args.max_stations)]
    run("iem_california.py", iem_args)

    gfs_args = ["--forecast-days", str(args.forecast_days)]
    if args.max_stations:
        # Add the six city locations to the station count allowance.
        gfs_args += ["--max-locations", str(args.max_stations + 6)]
    run("open_meteo_gfs.py", gfs_args)

    if args.with_hrrr:
        hrrr_args = ["--forecast-hours", *[str(x) for x in args.hrrr_hours]]
        if args.max_stations:
            hrrr_args += ["--max-stations", str(args.max_stations + 6)]
        run("noaa_hrrr_california.py", hrrr_args)

    print("\nCalifornia data-source collection finished.")
    print("Next: python validate_california_sources.py")


if __name__ == "__main__":
    main()
