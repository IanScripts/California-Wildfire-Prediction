
import csv
import os
from pathlib import Path

import requests

STATION = "CHOC1"

# Expanded date range to investigate precipitation
START = "202609280000"
END = "202610080000"

ROOT = Path(__file__).resolve().parent

OUTPUT_DIR = (
    ROOT / "live" / "california_sources"
    / "synoptic" / "chilao"
)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TOKEN = os.environ.get("SYNOPTIC_TOKEN")

if not TOKEN:
    raise SystemExit(
        "Missing SYNOPTIC_TOKEN environment variable."
    )

url = "https://api.synopticdata.com/v2/stations/timeseries"

params = {
    "token": TOKEN,
    "stid": STATION,
    "start": START,
    "end": END,
    "obtimezone": "UTC",
    "complete": "1",
    "sensorvars": "1",
    "qc_checks": "synopticlabs,madis",
}

print(f"Requesting {STATION}: {START} to {END}")

# Request weather observations
try:
    response = requests.get(
        url,
        params=params,
        timeout=120
    )
except requests.RequestException as exc:
    raise SystemExit(
        f"Unable to connect to Synoptic API: {type(exc).__name__}"
    )

# Avoid displaying the API token in HTTP errors
if not response.ok:
    raise SystemExit(
        f"Synoptic API returned HTTP {response.status_code}. "
        "Check your API token, account permissions, "
        "and request parameters."
    )

try:
    data = response.json()
except ValueError:
    raise SystemExit(
        "Synoptic API did not return valid JSON."
    )

# Display measurement units
print("\nSynoptic measurement units:")

units = data.get("UNITS", {})

if units:
    for variable, unit in units.items():
        print(f"  {variable}: {unit}")
else:
    print("  No unit metadata returned by the API.")

summary = data.get("SUMMARY", {})

if summary.get("RESPONSE_CODE") not in (1, "1"):
    raise SystemExit(
        "Synoptic API returned response code: "
        f"{summary.get('RESPONSE_CODE')}"
    )

stations = data.get("STATION", [])

if not stations:
    raise SystemExit(
        "No station data returned. "
        "Check token, station ID, and dates."
    )

station = next(
    (
        item for item in stations
        if item.get("STID") == STATION
    ),
    None
)

if station is None:
    raise SystemExit(
        f"Station {STATION} was not returned by the API."
    )

observations = station.get("OBSERVATIONS", {})
timestamps = observations.get("date_time", [])

if not timestamps:
    raise SystemExit(
        "Station returned no observation timestamps."
    )

# Preserve original Synoptic variable names
columns = ["station_id", "timestamp_utc"] + [
    key
    for key, value in observations.items()
    if key != "date_time" and isinstance(value, list)
]

# New filename preserves the original seven-day CSV
output = (
    OUTPUT_DIR /
    "chilao_test_20260928_20261007.csv"
)

with output.open(
    "w",
    newline="",
    encoding="utf-8"
) as file:

    writer = csv.DictWriter(
        file,
        fieldnames=columns
    )

    writer.writeheader()

    for i, timestamp in enumerate(timestamps):

        row = {
            "station_id": STATION,
            "timestamp_utc": timestamp,
        }

        for key in columns[2:]:
            values = observations.get(key, [])

            row[key] = (
                values[i]
                if i < len(values)
                else None
            )

        writer.writerow(row)

print("\nDownload completed successfully!")
print(f"Station: {station.get('NAME', STATION)}")
print(f"Observation timestamps: {len(timestamps)}")
print(f"Available observation variables: {len(columns) - 2}")
print(f"Saved: {output}")

print("\nFirst observation variables:")

for key in columns[2:20]:
    print(f"  {key}")

# Show positive precipitation values for investigation
print("\nNonzero precipitation observations:")

precip_values = observations.get(
    "precip_accum_set_1", []
)

nonzero_count = 0

for i, timestamp in enumerate(timestamps):
    if i >= len(precip_values):
        continue

    value = precip_values[i]

    try:
        amount = float(value)
    except (TypeError, ValueError):
        continue

    if amount > 0:
        print(f"  {timestamp}: {amount} mm")
        nonzero_count += 1

print(
    f"Total nonzero precipitation readings: "
    f"{nonzero_count}"
)

print(
    "\nNote: Precipitation values are raw Synoptic "
    "observations. Their accumulation interval "
    "has not yet been verified."
)
