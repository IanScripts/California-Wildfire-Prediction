
import csv
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

CLI = ROOT / "build" / "bin" / "NFDRS4_cli.exe"
INIT_FILE = ROOT / "data" / "NFDRSInitSample.txt"

WEATHER_DIR = ROOT / "live" / "fw21_history"
OUTPUT_DIR = ROOT / "live" / "nfdrs_results"
CONFIG_DIR = OUTPUT_DIR / "configs"

LOCATIONS = {
    "Los Angeles": "los_angeles",
    "Ventura": "ventura",
    "Riverside": "riverside",
    "San Bernardino": "san_bernardino",
    "Santa Ana": "santa_ana",
    "San Diego": "san_diego",
}

# Allow the historical dataset to grow over time.
MINIMUM_RECORDS = 24


def config_path(path):
    # Use forward slashes for NFDRS4 configuration.
    return path.resolve().as_posix()


def count_records(path):
    with path.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        return sum(1 for _ in csv.DictReader(file))


def create_config(slug):
    weather = WEATHER_DIR / f"{slug}_history.fw21"

    prefix = OUTPUT_DIR / slug

    config = CONFIG_DIR / f"{slug}.txt"

    all_file = Path(f"{prefix}_all.csv")
    index_file = Path(f"{prefix}_indexes.csv")
    moisture_file = Path(f"{prefix}_moistures.csv")
    state_file = Path(f"{prefix}_state.nfdrs")

    content = f"""# Open-Meteo historical NFDRS4 run
# Experimental: sample initialization parameters.

initFile = "{config_path(INIT_FILE)}";
wxFile = "{config_path(weather)}";

loadFromStateFile = "";
saveToStateFile = "{config_path(state_file)}";

allOutputsFile = "{config_path(all_file)}";
indexOutputFile = "{config_path(index_file)}";
fuelMoisturesOutputFile = "{config_path(moisture_file)}";

outputInterval = "0";
stationID = "open_meteo_{slug}";
useStoredOutputs = "0";
"""

    config.write_text(
        content, encoding="utf-8"
    )

    return config, weather, [
        all_file,
        index_file,
        moisture_file,
    ], state_file


def main():
    print("=" * 55)
    print("OPEN-METEO HISTORICAL NFDRS4 PROCESSING")
    print("=" * 55)

    if not CLI.is_file():
        raise FileNotFoundError(
            f"NFDRS4 executable not found: {CLI}"
        )

    if not INIT_FILE.is_file():
        raise FileNotFoundError(
            f"Initialization file not found: {INIT_FILE}"
        )

    OUTPUT_DIR.mkdir(
        parents=True, exist_ok=True
    )
    CONFIG_DIR.mkdir(
        parents=True, exist_ok=True
    )

    results = []

    for name, slug in LOCATIONS.items():
        print(f"\n{'=' * 50}")
        print(f"Processing: {name}")
        print("=" * 50)

        config, weather, outputs, state = create_config(slug)

        if not weather.is_file():
            print(f"ERROR: Missing weather file: {weather}")
            results.append((name, "MISSING WEATHER"))
            continue

        try:
            weather_count = count_records(weather)
        except (OSError, csv.Error) as error:
            print(f"ERROR: Cannot read weather file: {error}")
            results.append((name, "INVALID INPUT"))
            continue

        print(f"Input records: {weather_count}")

        # Accept growing historical datasets.
        if weather_count < MINIMUM_RECORDS:
            print(
                "ERROR: Insufficient weather records. "
                f"Minimum required: {MINIMUM_RECORDS}"
            )
            results.append((name, "INVALID INPUT"))
            continue

        print("Weather record count: ACCEPTED")

        # NFDRS4 appends to existing CSV outputs.
        # Remove old generated results before rerunning.
        for path in outputs + [state]:
            if path.exists():
                path.unlink()

        print("Running NFDRS4...")

        try:
            process = subprocess.run(
                [str(CLI), str(config)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=180,
            )

        except (
            subprocess.TimeoutExpired,
            OSError,
        ) as error:
            print(f"ERROR: {error}")
            results.append((name, "FAILED"))
            continue

        if process.stdout.strip():
            print(process.stdout.strip())

        if process.stderr.strip():
            print(process.stderr.strip())

        if process.returncode != 0:
            print(
                f"ERROR: NFDRS4 exited with code "
                f"{process.returncode}"
            )
            results.append((name, "FAILED"))
            continue

        valid = True

        # Verify all expected output files.
        for output in outputs:
            if not output.is_file():
                print(f"ERROR: Missing {output.name}")
                valid = False
                continue

            try:
                count = count_records(output)
            except (OSError, csv.Error) as error:
                print(
                    f"ERROR: Cannot read {output.name}: {error}"
                )
                valid = False
                continue

            print(
                f"{output.name}: {count} records"
            )

            # Compare against the actual input size,
            # rather than a hard-coded number.
            if count != weather_count:
                print(
                    "ERROR: Output count mismatch. "
                    f"Expected {weather_count}, "
                    f"received {count}."
                )
                valid = False
            else:
                print("Record count: MATCH")

        # Verify NFDRS4 saved its state.
        if not state.is_file():
            print("ERROR: Missing saved state.")
            valid = False
        else:
            print(f"Saved state: {state.name}")

        status = "PASS" if valid else "CHECK OUTPUT"
        results.append((name, status))

        print(f"Result: {status}")

    print("\n" + "=" * 50)
    print("NFDRS4 RUN SUMMARY")
    print("=" * 50)

    for name, status in results:
        print(f"{name}: {status}")

    passed = sum(
        status == "PASS"
        for _, status in results
    )

    print(
        f"\nSuccessful locations: "
        f"{passed}/{len(LOCATIONS)}"
    )

    if passed == len(LOCATIONS):
        print("\nHistorical NFDRS4 processing completed.")
    else:
        print("\nSome locations require attention.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
