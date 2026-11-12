
import csv
import math
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "live" / "open_meteo_combined_socal.csv"
OUTPUT = ROOT / "live" / "fw21_history"

COLUMNS = [
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

REQUIRED = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "shortwave_radiation",
]


def number(value):
    if value is None or value.strip() == "":
        return None

    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def convert(row):
    values = {
        field: number(row.get(field))
        for field in REQUIRED
    }

    if any(value is None for value in values.values()):
        return None

    temp = values["temperature_2m"]
    humidity = values["relative_humidity_2m"]
    precip = values["precipitation"]
    wind = values["wind_speed_10m"]
    direction = values["wind_direction_10m"]
    solar = values["shortwave_radiation"]

    if not 0 <= humidity <= 100:
        return None

    if precip < 0 or wind < 0 or solar < 0:
        return None

    if not 0 <= direction <= 360:
        return None

    timestamp = datetime.fromisoformat(
        row["time"].replace("Z", "+00:00")
    )

    # Input timestamps are UTC.
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    snow = number(row.get("snowfall"))

    return {
        "DateTime": timestamp.strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        ),
        "Temperature(F)": round(temp, 2),
        "RelativeHumidity(%)": round(humidity, 2),
        "Precipitation(in)": round(precip, 4),
        "WindSpeed(mph)": round(wind, 2),
        "WindAzimuth(degrees)": round(direction % 360, 2),
        "SolarRadiation(W/m2)": round(solar, 2),
        "SnowFlag": 1 if snow is not None and snow > 0 else 0,
        "GustSpeed(mph)": "",
        "GustAzimuth(degrees)": "",
    }


def main():
    if not INPUT.exists():
        raise FileNotFoundError(INPUT)

    locations = {}
    skipped = 0
    forecast_excluded = 0

    with INPUT.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            if row["data_type"] == "forecast":
                forecast_excluded += 1
                continue

            converted = convert(row)

            if converted is None:
                skipped += 1
                continue

            location = row["location"].strip()
            locations.setdefault(location, []).append(
                converted
            )

    if skipped:
        raise RuntimeError(
            f"{skipped} historical records have invalid "
            "weather measurements. Resolve these before "
            "running NFDRS4."
        )

    OUTPUT.mkdir(parents=True, exist_ok=True)

    for location, records in locations.items():
        records.sort(key=lambda r: r["DateTime"])

        filename = (
            location.lower().replace(" ", "_")
            + "_history.fw21"
        )

        path = OUTPUT / filename

        with path.open(
            "w", encoding="utf-8", newline=""
        ) as file:
            writer = csv.DictWriter(
                file, fieldnames=COLUMNS
            )
            writer.writeheader()
            writer.writerows(records)

        print(f"{location}: {len(records)} records")
        print(f"Saved: {path}")

    print()
    print("Historical FW21 preparation complete.")
    print("Locations:", len(locations))
    print("Forecast records excluded:", forecast_excluded)
    print("Invalid historical records:", skipped)


if __name__ == "__main__":
    main()
