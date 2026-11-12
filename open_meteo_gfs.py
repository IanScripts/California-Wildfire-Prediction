"""
Collect GFS forecast weather for every discovered California IEM ASOS station
plus the six original Southern California project coordinates.

Run:
  python open_meteo_gfs.py
  python open_meteo_gfs.py --forecast-days 7 --max-locations 5

This is a model forecast dataset, not station observations. It is stored separately
from the existing Open-Meteo archive and NFDRS4 pipeline.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "live" / "california_sources"
IEM_CATALOG = SOURCE_DIR / "iem" / "iem_california_station_catalog.csv"
OUT_DIR = SOURCE_DIR / "open_meteo_gfs"
OUT_FILE = OUT_DIR / "open_meteo_gfs_california_forecast.csv"
API_URL = "https://api.open-meteo.com/v1/gfs"

SIX_SOUTHERN_CALIFORNIA = {
    "Los Angeles": (34.0522, -118.2437),
    "Ventura": (34.2805, -119.2945),
    "Riverside": (33.9806, -117.3755),
    "San Bernardino": (34.1083, -117.2898),
    "Santa Ana": (33.7455, -117.8677),
    "San Diego": (32.7157, -117.1611),
}

# Forecast fields relevant to fire-weather conditions. Values are requested in
# consistent units; response hourly_units is retained in a companion metadata file.
HOURLY = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation",
    "rain",
    "showers",
    "snowfall",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "shortwave_radiation",
    "pressure_msl",
    "cloud_cover",
    "vapour_pressure_deficit",
    "soil_temperature_0_to_7cm",
    "soil_moisture_0_to_7cm",
    "cape",
]


def load_locations() -> list[dict]:
    locations = []
    if IEM_CATALOG.exists():
        stations = pd.read_csv(IEM_CATALOG)
        for row in stations.to_dict("records"):
            locations.append({
                "location_id": f"IEM_{row['station_id']}",
                "location_name": str(row.get("station_name") or row["station_id"]),
                "station_id": str(row["station_id"]),
                "network": str(row.get("network", "CA_ASOS")),
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "location_type": "weather_station",
            })
    else:
        print("WARNING: IEM station catalog not found; GFS will use the six project locations only.")
    for name, (lat, lon) in SIX_SOUTHERN_CALIFORNIA.items():
        locations.append({
            "location_id": "CITY_" + name.lower().replace(" ", "_"),
            "location_name": name,
            "station_id": "",
            "network": "",
            "latitude": lat,
            "longitude": lon,
            "location_type": "project_city_coordinate",
        })

    # Avoid duplicate requests where an IEM station is very close to a project city point.
    unique = {}
    for item in locations:
        key = (round(item["latitude"], 4), round(item["longitude"], 4))
        unique.setdefault(key, item)
    return list(unique.values())


def fetch_one(location: dict, forecast_days: int) -> tuple[pd.DataFrame, dict]:
    params = {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "hourly": ",".join(HOURLY),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "UTC",
        "forecast_days": forecast_days,
    }
    response = requests.get(API_URL, params=params, timeout=90)
    response.raise_for_status()
    payload = response.json()
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict) or "time" not in hourly:
        raise ValueError(f"Unexpected GFS response: {str(payload)[:500]}")
    frame = pd.DataFrame(hourly)
    frame["location_id"] = location["location_id"]
    frame["location_name"] = location["location_name"]
    frame["station_id"] = location["station_id"]
    frame["network"] = location["network"]
    frame["location_type"] = location["location_type"]
    frame["requested_latitude"] = location["latitude"]
    frame["requested_longitude"] = location["longitude"]
    frame["grid_latitude"] = payload.get("latitude")
    frame["grid_longitude"] = payload.get("longitude")
    frame["grid_elevation_m"] = payload.get("elevation")
    frame["model"] = "NOAA GFS via Open-Meteo"
    frame["source"] = "Open-Meteo GFS API"
    frame["data_type"] = "forecast_model"
    frame["retrieved_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    metadata = {
        "location_id": location["location_id"],
        "location_name": location["location_name"],
        "hourly_units": payload.get("hourly_units", {}),
        "timezone": payload.get("timezone"),
        "utc_offset_seconds": payload.get("utc_offset_seconds"),
        "generationtime_ms": payload.get("generationtime_ms"),
    }
    return frame, metadata


def main():
    parser = argparse.ArgumentParser(description="Collect Open-Meteo GFS forecasts for California.")
    parser.add_argument("--forecast-days", type=int, default=16, choices=range(1, 17))
    parser.add_argument("--max-locations", type=int, default=None,
                        help="Optional test limit; omit to cover all discovered stations and city points.")
    args = parser.parse_args()

    locations = load_locations()
    if args.max_locations:
        locations = locations[:args.max_locations]
    if not locations:
        raise SystemExit("No locations available.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frames, metadata, failures = [], [], []
    for index, location in enumerate(locations, start=1):
        print(f"[{index}/{len(locations)}] GFS: {location['location_name']} ({location['location_id']})")
        try:
            frame, info = fetch_one(location, args.forecast_days)
            frames.append(frame)
            metadata.append(info)
            print(f"  Retrieved {len(frame)} hourly forecast rows")
        except (requests.RequestException, ValueError, KeyError) as exc:
            failures.append((location["location_id"], str(exc)))
            print(f"  FAILED: {exc}")
        time.sleep(0.15)

    if frames:
        result = pd.concat(frames, ignore_index=True)
        result.to_csv(OUT_FILE, index=False)
        print(f"\nSaved: {OUT_FILE}")
        print(f"Forecast rows: {len(result)}")
        print(f"Locations successful: {result.location_id.nunique()}")
        print(f"Variables: {', '.join(c for c in HOURLY if c in result.columns)}")
        pd.DataFrame([
            {"location_id": x["location_id"], "location_name": x["location_name"],
             "hourly_units": str(x["hourly_units"]), "timezone": x["timezone"],
             "utc_offset_seconds": x["utc_offset_seconds"]}
            for x in metadata
        ]).to_csv(OUT_DIR / "gfs_response_metadata.csv", index=False)
    else:
        print("No GFS data retrieved.")
    if failures:
        pd.DataFrame(failures, columns=["location_id", "error"]).to_csv(
            OUT_DIR / "gfs_failures.csv", index=False
        )
        raise SystemExit(f"{len(failures)} locations failed. See gfs_failures.csv.")


if __name__ == "__main__":
    main()
