
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "live"

# CIMIS REST API endpoint
API_URL = "https://et.water.ca.gov/api/data"

# Initial connection test station
TEST_STATION = "2"

DATA_ITEMS = [
    "hly-air-tmp",
    "hly-rel-hum",
    "hly-precip",
    "hly-wind-spd",
    "hly-wind-dir",
    "hly-sol-rad",
]

FIELD_MAP = {
    "HlyAirTmp": "temperature_f",
    "HlyRelHum": "relative_humidity_pct",
    "HlyPrecip": "precipitation_in",
    "HlyWindSpd": "wind_speed_mph",
    "HlyWindDir": "wind_direction_degrees",
    "HlySolRad": "solar_radiation_raw",
}


def measurement(record, field):
    item = record.get(field)

    if not isinstance(item, dict):
        return None

    value = item.get("Value")

    if value in (None, ""):
        return None

    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def main():
    key = os.getenv("CIMIS_API_KEY")

    if not key:
        raise SystemExit(
            "Missing CIMIS_API_KEY environment variable."
        )

    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=3)

    params = {
        "appKey": key,
        "targets": TEST_STATION,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "unitOfMeasure": "E",
        "dataItems": ",".join(DATA_ITEMS),
    }

    print("Connecting to CIMIS...")
    print("Test station:", TEST_STATION)

    try:
        response = requests.get(
            API_URL,
            params=params,
            headers={"Accept": "application/json"},
            timeout=45,
        )

        print("HTTP status:", response.status_code)

        # Print response information for debugging.
        # Do not print the request URL because it contains the API key.
        print(
            "Content type:",
            response.headers.get("Content-Type")
        )

        print(
            "Response length:",
            len(response.content)
        )

        # Redact the API key if it appears in the response.
        preview = response.text[:500].replace(
            key, "[REDACTED]"
        )

        print("Response preview:", preview)

        if not response.ok:
            print("CIMIS returned an HTTP error.")
            return

        # Check for empty response
        if not response.content.strip():
            print("CIMIS returned an empty response.")
            return

        # Attempt to parse JSON
        try:
            data = response.json()

        except ValueError:
            print(
                "Response was not valid JSON. "
                "Check the content type and preview above."
            )
            return

    except requests.RequestException as error:
        print(
            "Connection failed:",
            type(error).__name__
        )
        return

    # Validate the JSON structure
    if not isinstance(data, dict):
        print("Unexpected JSON response format.")
        return

    providers = data.get("Data", {}).get("Providers", [])

    rows = []

    for provider in providers:
        for record in provider.get("Records", []):

            if record.get("Scope") != "hourly":
                continue

            row = {
                "station_id": record.get("Station"),
                "date": record.get("Date"),
                "hour": record.get("Hour"),
                "source": "CIMIS",
                "data_type": "observed",
            }

            for source_field, output_field in FIELD_MAP.items():
                row[output_field] = measurement(
                    record,
                    source_field
                )

            rows.append(row)

    if not rows:
        print("No hourly observations returned.")
        print("Response keys:", list(data.keys()))
        print(
            "Check the station ID, date range, "
            "and API endpoint."
        )
        return

    df = pd.DataFrame(rows)

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output = OUTPUT_DIR / "cimis_socal.csv"

    df.to_csv(
        output,
        index=False
    )

    print()
    print("CIMIS API connection successful!")
    print(
        "Successfully retrieved",
        len(df),
        "observations"
    )
    print("Saved:", output)
    print(
        "Stations:",
        df["station_id"].nunique()
    )
    print(
        "Columns:",
        list(df.columns)
    )


if __name__ == "__main__":
    main()
