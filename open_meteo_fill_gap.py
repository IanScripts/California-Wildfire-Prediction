
import requests
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent

OUTPUT_DIR = ROOT / "live"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

URL = "https://api.open-meteo.com/v1/forecast"

LOCATIONS = {
    "Los Angeles": (34.0522, -118.2437),
    "Ventura": (34.2805, -119.2945),
    "Riverside": (33.9806, -117.3755),
    "San Bernardino": (34.1083, -117.2898),
    "Santa Ana": (33.7455, -117.8677),
    "San Diego": (32.7157, -117.1611),
}

HOURLY = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "shortwave_radiation",
    "snowfall",
]


def main():
    frames = []

    for name, (lat, lon) in LOCATIONS.items():
        print(f"Fetching missing weather: {name}")

        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": ",".join(HOURLY),
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "UTC",
            "start_date": "2026-10-24",
            "end_date": "2026-10-27",
        }

        try:
            response = requests.get(
                URL,
                params=params,
                timeout=60
            )
            response.raise_for_status()

            data = response.json()
            df = pd.DataFrame(data["hourly"])

            df["location"] = name
            df["latitude"] = lat
            df["longitude"] = lon
            df["source"] = "Open-Meteo Forecast API"
            df["data_type"] = "historical_modeled"

            frames.append(df)

            print(f"  Retrieved {len(df)} records")

        except (
            requests.RequestException,
            ValueError,
            KeyError
        ) as error:
            print(f"  Failed: {error}")

    if not frames:
        print("No gap weather downloaded.")
        return

    combined = pd.concat(
        frames,
        ignore_index=True
    )

    output = (
        OUTPUT_DIR / "open_meteo_gap_socal.csv"
    )

    combined.to_csv(
        output,
        index=False
    )

    print(f"\nSaved: {output}")
    print(f"Total records: {len(combined)}")
    print(
        f"Locations: {combined['location'].nunique()}"
    )


if __name__ == "__main__":
    main()
