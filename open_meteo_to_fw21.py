
import csv
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent

INPUT_FILE = ROOT / "live" / "open_meteo_socal.csv"
OUTPUT_DIR = ROOT / "live" / "fw21"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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


def to_float(value):
    if value is None or value.strip() == "":
        return None
    return float(value)


def convert_row(row):
    # Open-Meteo API already returns:
    # Temperature: Fahrenheit
    # Precipitation: inches
    # Wind speed: mph
    # Relative humidity: percentage
    # Wind direction: degrees
    # Solar radiation: W/m2
    # Snowfall: centimeters
    # Time: UTC

    temp_f = to_float(row["temperature_2m"])
    humidity = to_float(row["relative_humidity_2m"])
    precip_in = to_float(row["precipitation"])
    wind_mph = to_float(row["wind_speed_10m"])
    wind_dir = to_float(row["wind_direction_10m"])
    solar = to_float(row["shortwave_radiation"])
    snowfall_cm = to_float(row["snowfall"])

    required = [
        temp_f,
        humidity,
        precip_in,
        wind_mph,
        wind_dir,
        solar,
    ]

    # Skip records with missing required weather values.
    if any(value is None for value in required):
        return None

    # Validate weather measurements.
    if not 0 <= humidity <= 100:
        return None

    if precip_in < 0 or wind_mph < 0 or solar < 0:
        return None

    if not 0 <= wind_dir <= 360:
        return None

    # Open-Meteo timestamps are requested in UTC.
    timestamp = datetime.fromisoformat(row["time"])

    # Determine whether snow was reported.
    snow_flag = (
        1
        if snowfall_cm is not None and snowfall_cm > 0
        else 0
    )

    # Create an FW21 weather record.
    # Units already match the FW21 columns.
    return {
        "DateTime": timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
        "Temperature(F)": round(temp_f, 2),
        "RelativeHumidity(%)": round(humidity, 2),
        "Precipitation(in)": round(precip_in, 4),
        "WindSpeed(mph)": round(wind_mph, 2),
        "WindAzimuth(degrees)": round(wind_dir % 360, 2),
        "SolarRadiation(W/m2)": round(solar, 2),
        "SnowFlag": snow_flag,
        "GustSpeed(mph)": "",
        "GustAzimuth(degrees)": "",
    }


def main():
    if not INPUT_FILE.exists():
        print("ERROR: Open-Meteo CSV not found.")
        print(f"Expected file: {INPUT_FILE}")
        return

    locations = {}
    skipped = 0
    total_rows = 0

    with INPUT_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:
            total_rows += 1

            location = row["location"].strip()
            converted = convert_row(row)

            if converted is None:
                skipped += 1
                continue

            locations.setdefault(
                location, []
            ).append(converted)

    # Create one FW21 file per location.
    for location, records in locations.items():

        filename = (
            location.lower().replace(" ", "_")
            + ".fw21"
        )

        output_file = OUTPUT_DIR / filename

        # Ensure records are ordered chronologically.
        records.sort(
            key=lambda record: record["DateTime"]
        )

        with output_file.open(
            "w",
            encoding="utf-8",
            newline=""
        ) as file:

            writer = csv.DictWriter(
                file,
                fieldnames=FW21_COLUMNS
            )

            writer.writeheader()
            writer.writerows(records)

        print(f"{location}: {len(records)} records")
        print(f"Saved: {output_file}")
        print()

    print("Conversion complete.")
    print("Total input rows:", total_rows)
    print("Locations:", len(locations))
    print("Skipped rows:", skipped)
    print(
        "Total converted rows:",
        total_rows - skipped
    )

    print()
    print("IMPORTANT:")
    print("Weather timestamps are UTC.")
    print("Temperature is Fahrenheit.")
    print("Precipitation is inches.")
    print("Wind speed is mph.")
    print("Solar radiation is W/m2.")


if __name__ == "__main__":
    main()
