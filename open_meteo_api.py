
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parent

OUTPUT_DIR = ROOT / "live"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOCATIONS = {
    "Los Angeles": (34.0522, -118.2437),
    "Ventura": (34.2805, -119.2945),
    "Riverside": (33.9806, -117.3755),
    "San Bernardino": (34.1083, -117.2898),
    "Santa Ana": (33.7455, -117.8677),
    "San Diego": (32.7157, -117.1611),
}

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

HISTORICAL_URL = (
    "https://archive-api.open-meteo.com/v1/archive"
)

HOURLY = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "shortwave_radiation",
    "snowfall",
]

COMMON_PARAMS = {
    "hourly": ",".join(HOURLY),
    "temperature_unit": "fahrenheit",
    "wind_speed_unit": "mph",
    "precipitation_unit": "inch",
    "timezone": "UTC",
}


def request_weather(url, params):
    response = requests.get(
        url,
        params=params,
        timeout=60
    )

    response.raise_for_status()

    data = response.json()

    if "hourly" not in data:
        raise ValueError(
            f"Missing hourly data: {data}"
        )

    return pd.DataFrame(data["hourly"])


def get_recent_weather(name, latitude, longitude):
    params = {
        **COMMON_PARAMS,
        "latitude": latitude,
        "longitude": longitude,
        "past_days": 2,
        "forecast_days": 1,
    }

    df = request_weather(FORECAST_URL, params)

    # Open-Meteo forecast endpoint includes
    # recent modeled weather and future forecasts.
    current_hour = pd.Timestamp.now(
        tz="UTC"
    ).floor("h").tz_localize(None)

    timestamps = pd.to_datetime(df["time"])

    df["data_type"] = "historical_modeled"
    df.loc[
        timestamps > current_hour,
        "data_type"
    ] = "forecast"

    df["location"] = name
    df["latitude"] = latitude
    df["longitude"] = longitude
    df["source"] = "Open-Meteo Forecast API"

    return df


def get_historical_weather(
    name,
    latitude,
    longitude,
    start_date,
    end_date
):
    params = {
        **COMMON_PARAMS,
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
    }

    df = request_weather(HISTORICAL_URL, params)

    df["location"] = name
    df["latitude"] = latitude
    df["longitude"] = longitude
    df["source"] = "Open-Meteo Historical API"
    df["data_type"] = "historical_reanalysis"

    return df


def download_recent():
    print("\nDownloading recent weather...")

    frames = []

    for name, (lat, lon) in LOCATIONS.items():
        print(f"Fetching recent weather: {name}")

        try:
            df = get_recent_weather(
                name, lat, lon
            )

            frames.append(df)

            print(
                f"  Retrieved {len(df)} records"
            )

        except (
            requests.RequestException,
            ValueError,
            KeyError
        ) as error:
            print(f"  Failed: {error}")

    if not frames:
        print("No recent weather retrieved.")
        return

    combined = pd.concat(
        frames,
        ignore_index=True
    )

    output = (
        OUTPUT_DIR / "open_meteo_socal.csv"
    )

    combined.to_csv(
        output,
        index=False
    )

    print(f"\nSaved recent weather: {output}")
    print(f"Records: {len(combined)}")


def download_historical():
    print("\nDownloading historical weather...")

    # Use a historical period ending several
    # days ago to avoid archive data latency.
    today = datetime.now(
        timezone.utc
    ).date()

    end_date = today - timedelta(days=7)
    start_date = end_date - timedelta(days=89)

    print(f"Start date: {start_date}")
    print(f"End date: {end_date}")

    frames = []

    for name, (lat, lon) in LOCATIONS.items():
        print(f"Fetching historical weather: {name}")

        try:
            df = get_historical_weather(
                name,
                lat,
                lon,
                start_date.isoformat(),
                end_date.isoformat()
            )

            frames.append(df)

            print(
                f"  Retrieved {len(df)} records"
            )

        except (
            requests.RequestException,
            ValueError,
            KeyError
        ) as error:
            print(f"  Failed: {error}")

    if not frames:
        print("No historical weather retrieved.")
        return

    combined = pd.concat(
        frames,
        ignore_index=True
    )

    output = (
        OUTPUT_DIR /
        "open_meteo_historical_socal.csv"
    )

    combined.to_csv(
        output,
        index=False
    )

    print(f"\nSaved historical weather: {output}")
    print(f"Records: {len(combined)}")


def main():
    print("=" * 50)
    print("OPEN-METEO SOUTHERN CALIFORNIA WEATHER")
    print("=" * 50)

    download_recent()
    download_historical()

    print("\nDownload process finished.")


if __name__ == "__main__":
    main()
