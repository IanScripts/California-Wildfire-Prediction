
import csv
import math
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "live" / "open_meteo_combined_socal.csv"
CLI = ROOT / "build" / "bin" / "NFDRS4_cli.exe"
INIT = ROOT / "data" / "NFDRSInitSample.txt"

HISTORY_DIR = ROOT / "live" / "fw21_history"
STATE_DIR = ROOT / "live" / "nfdrs_results"
OUTPUT_DIR = ROOT / "live" / "nfdrs_forecast"
CONFIG_DIR = OUTPUT_DIR / "configs"
WEATHER_DIR = OUTPUT_DIR / "fw21"

LOCATIONS = {
    "Los Angeles": "los_angeles",
    "Ventura": "ventura",
    "Riverside": "riverside",
    "San Bernardino": "san_bernardino",
    "Santa Ana": "santa_ana",
    "San Diego": "san_diego",
}

FW21_COLUMNS = [
    "DateTime",
    "Temperature(F)",
    "RelativeHumidity(%)",
    "Precipitation(in)",
    "WindSpeed(mph)",
    "WindAzimuth(degrees)",
    "SolarRadiation(W/m2)",
    "SnowFlag",
    "GustSpeed(mph)",
    "GustAzimuth(degrees)",
]


def timestamp(value):
    dt = datetime.fromisoformat(
        value.strip().replace("Z", "+00:00")
    )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def number(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def convert(row):
    fields = [
        "temperature_2m",
        "relative_humidity_2m",
        "precipitation",
        "wind_speed_10m",
        "wind_direction_10m",
        "shortwave_radiation",
    ]

    values = {field: number(row.get(field)) for field in fields}

    if any(value is None for value in values.values()):
        raise ValueError("Missing or invalid forecast measurement")

    humidity = values["relative_humidity_2m"]
    precipitation = values["precipitation"]
    wind = values["wind_speed_10m"]
    direction = values["wind_direction_10m"]
    solar = values["shortwave_radiation"]

    if not 0 <= humidity <= 100:
        raise ValueError("Invalid humidity")
    if precipitation < 0 or wind < 0 or solar < 0:
        raise ValueError("Invalid precipitation, wind, or solar")
    if not 0 <= direction <= 360:
        raise ValueError("Invalid wind direction")

    snow = number(row.get("snowfall"))

    # Open-Meteo was already requested in
    # Fahrenheit, inches, and mph.
    return {
        "DateTime": timestamp(row["time"]).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        ),
        "Temperature(F)": values["temperature_2m"],
        "RelativeHumidity(%)": humidity,
        "Precipitation(in)": precipitation,
        "WindSpeed(mph)": wind,
        "WindAzimuth(degrees)": direction % 360,
        "SolarRadiation(W/m2)": solar,
        "SnowFlag": 1 if snow is not None and snow > 0 else 0,
        "GustSpeed(mph)": "",
        "GustAzimuth(degrees)": "",
    }


def count_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def main():
    for path in [INPUT, CLI, INIT]:
        if not path.is_file():
            raise FileNotFoundError(path)

    for directory in [OUTPUT_DIR, CONFIG_DIR, WEATHER_DIR]:
        directory.mkdir(parents=True, exist_ok=True)

    with INPUT.open("r", encoding="utf-8-sig", newline="") as f:
        all_rows = list(csv.DictReader(f))

    summary = []

    for name, slug in LOCATIONS.items():
        print(f"\nProcessing forecast: {name}")

        historical_fw21 = (
            HISTORY_DIR / f"{slug}_history.fw21"
        )
        historical_state = (
            STATE_DIR / f"{slug}_state.nfdrs"
        )

        if not historical_fw21.is_file():
            print("FAILED: Historical FW21 file missing")
            summary.append((name, "FAILED"))
            continue

        if not historical_state.is_file():
            print("FAILED: Historical NFDRS state missing")
            summary.append((name, "FAILED"))
            continue

        historical_times = []
        with historical_fw21.open(
            "r", encoding="utf-8-sig", newline=""
        ) as f:
            for row in csv.DictReader(f, skipinitialspace=True):
                historical_times.append(timestamp(row["DateTime"]))

        if not historical_times:
            print("FAILED: No historical weather")
            summary.append((name, "FAILED"))
            continue

        last_history = max(historical_times)

        forecast_rows = [
            row for row in all_rows
            if row["location"] == name
            and row["data_type"] == "forecast"
        ]

        forecast_rows.sort(key=lambda r: timestamp(r["time"]))

        if not forecast_rows:
            print("FAILED: No forecast records")
            summary.append((name, "FAILED"))
            continue

        forecast_times = [timestamp(r["time"]) for r in forecast_rows]

        if forecast_times[0] != last_history + timedelta(hours=1):
            print("FAILED: Forecast does not immediately follow history")
            print("Historical end:", last_history)
            print("Forecast start:", forecast_times[0])
            summary.append((name, "FAILED"))
            continue

        if any(
            later - earlier != timedelta(hours=1)
            for earlier, later in zip(
                forecast_times, forecast_times[1:]
            )
        ):
            print("FAILED: Forecast contains missing or duplicate hours")
            summary.append((name, "FAILED"))
            continue

        try:
            converted = [convert(row) for row in forecast_rows]
        except ValueError as error:
            print(f"FAILED: {error}")
            summary.append((name, "FAILED"))
            continue

        weather_file = WEATHER_DIR / f"{slug}_forecast.fw21"

        with weather_file.open(
            "w", encoding="utf-8", newline=""
        ) as f:
            writer = csv.DictWriter(f, fieldnames=FW21_COLUMNS)
            writer.writeheader()
            writer.writerows(converted)

        prefix = OUTPUT_DIR / slug
        all_output = Path(f"{prefix}_forecast_all.csv")
        indexes = Path(f"{prefix}_forecast_indexes.csv")
        moistures = Path(f"{prefix}_forecast_moistures.csv")
        forecast_state = Path(f"{prefix}_forecast_state.nfdrs")
        config_file = CONFIG_DIR / f"{slug}_forecast.txt"

        config = f'''# Experimental Open-Meteo forecast run

initFile = "{INIT.as_posix()}";
wxFile = "{weather_file.as_posix()}";

loadFromStateFile = "{historical_state.as_posix()}";
saveToStateFile = "{forecast_state.as_posix()}";

allOutputsFile = "{all_output.as_posix()}";
indexOutputFile = "{indexes.as_posix()}";
fuelMoisturesOutputFile = "{moistures.as_posix()}";

outputInterval = "0";
stationID = "open_meteo_{slug}";
useStoredOutputs = "0";
'''

        config_file.write_text(config, encoding="utf-8")

        # NFDRS4 appends to existing CSV files.
        # Remove only old forecast outputs.
        for path in [
            all_output, indexes, moistures, forecast_state
        ]:
            if path.exists():
                path.unlink()

        print("Historical end:", last_history)
        print("Forecast start:", forecast_times[0])
        print("Forecast hours:", len(converted))

        try:
            process = subprocess.run(
                [str(CLI), str(config_file)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"FAILED: {error}")
            summary.append((name, "FAILED"))
            continue

        if process.stdout.strip():
            print(process.stdout.strip())
        if process.stderr.strip():
            print(process.stderr.strip())

        valid = process.returncode == 0

        for output in [all_output, indexes, moistures]:
            if output.is_file():
                count = count_csv(output)
                print(f"{output.name}: {count} records")
                if count != len(converted):
                    valid = False
            else:
                print(f"Missing: {output.name}")
                valid = False

        if not forecast_state.is_file():
            print("Missing forecast state")
            valid = False

        status = "PASS" if valid else "FAILED"
        summary.append((name, status))

    print("\n" + "=" * 50)
    print("NFDRS4 FORECAST SUMMARY")
    print("=" * 50)

    for name, status in summary:
        print(f"{name}: {status}")

    passed = sum(status == "PASS" for _, status in summary)
    print(f"\nSuccessful locations: {passed}/{len(LOCATIONS)}")

    if passed != len(LOCATIONS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
