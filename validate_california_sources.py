"""Validate downloaded California source datasets without modifying them."""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "live" / "california_sources"

def report(label, path, required=()):
    print(f"\n{label}")
    print("-" * len(label))
    if not path.exists():
        print("NOT FOUND:", path)
        return
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception as exc:
        print("UNREADABLE:", exc)
        return
    print("Rows:", len(df))
    print("Columns:", ", ".join(df.columns))
    missing = [c for c in required if c not in df.columns]
    print("Required columns missing:", missing if missing else "none")
    if "station_id" in df:
        print("Unique station IDs:", df["station_id"].nunique())
    for candidate in ("valid", "time", "valid_time_utc"):
        if candidate in df:
            ts = pd.to_datetime(df[candidate], utc=True, errors="coerce")
            if ts.notna().any():
                print("Time coverage:", ts.min(), "to", ts.max())
            break
    if "source" in df:
        print("Sources:", df["source"].dropna().unique()[:8].tolist())
    if "data_type" in df:
        print("Data types:", df["data_type"].value_counts(dropna=False).to_dict())

def main():
    report("IEM California station catalog",
           BASE / "iem" / "iem_california_station_catalog.csv",
           ("station_id", "latitude", "longitude", "network"))
    report("IEM station observations",
           BASE / "iem" / "iem_california_observations.csv",
           ("station_id", "source", "data_type"))
    report("Open-Meteo GFS forecasts",
           BASE / "open_meteo_gfs" / "open_meteo_gfs_california_forecast.csv",
           ("time", "temperature_2m", "relative_humidity_2m",
            "wind_speed_10m", "wind_direction_10m", "location_id", "model"))
    report("NOAA HRRR forecasts",
           BASE / "noaa_hrrr" / "noaa_hrrr_california_forecast.csv",
           ("station_id", "valid_time_utc", "model", "forecast_hour"))

if __name__ == "__main__":
    main()
