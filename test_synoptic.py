
import os
import requests

TOKEN = os.getenv("SYNOPTIC_TOKEN")

if not TOKEN:
    raise SystemExit(
        "ERROR: SYNOPTIC_TOKEN environment variable not found."
    )

URL = "https://api.synopticdata.com/v2/stations/metadata"

params = {
    "token": TOKEN,
    "radius": "34.0522,-118.2437,25",
    "status": "active",
    "complete": 1,
    "sensorvars": 1,
}

print("Connecting to Synoptic Data...")
print("Searching active stations near Los Angeles...")

try:
    response = requests.get(
        URL,
        params=params,
        timeout=60
    )

    response.raise_for_status()
    data = response.json()

    summary = data.get("SUMMARY", {})

    if summary.get("RESPONSE_CODE") != 1:
        raise RuntimeError(
            summary.get("RESPONSE_MESSAGE", "API request failed")
        )

    stations = data.get("STATION", [])

    print("\nCONNECTION SUCCESSFUL")
    print("Stations found:", len(stations))

    for station in stations[:10]:
        print(
            station.get("STID"),
            "|",
            station.get("NAME"),
            "|",
            station.get("LATITUDE"),
            station.get("LONGITUDE"),
        )

    print("\nStation metadata successfully retrieved.")

except Exception as error:
    print("\nCONNECTION FAILED")
    print(error)
