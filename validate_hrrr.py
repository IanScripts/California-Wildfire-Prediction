
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent

DATA_FILE = (
    ROOT / "live" / "california_sources" /
    "noaa_hrrr" / "noaa_hrrr_california_forecast.csv"
)

CATALOG_FILE = (
    ROOT / "live" / "california_sources" /
    "iem" / "iem_california_station_catalog.csv"
)

EXPECTED_LOCATIONS = 167
EXPECTED_HOURS = {0, 1, 2, 3, 4, 5}

WEATHER_FIELDS = [
    "temperature_2m_k",
    "dew_point_2m_k",
    "relative_humidity_2m_pct",
    "u_wind_10m_ms",
    "v_wind_10m_ms",
    "wind_gust_ms",
    "precipitation_accum_kg_m2",
    "shortwave_radiation_w_m2",
    "surface_pressure_pa",
]

DERIVED_FIELDS = [
    "wind_speed_10m_ms",
    "wind_direction_10m_deg",
]

CITY_IDS = {
    "CITY_los_angeles",
    "CITY_ventura",
    "CITY_riverside",
    "CITY_san_bernardino",
    "CITY_santa_ana",
    "CITY_san_diego",
}


def report(label, passed, details=""):
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {label}")
    if details:
        print(f"       {details}")


def main():
    print("=" * 65)
    print("NOAA HRRR DATA VALIDATION")
    print("=" * 65)

    if not DATA_FILE.exists():
        raise SystemExit(f"Missing forecast file: {DATA_FILE}")

    df = pd.read_csv(DATA_FILE, low_memory=False)

    required = [
        "station_id",
        "initialization_time_utc",
        "forecast_hour",
        "valid_time_utc",
        *WEATHER_FIELDS,
        *DERIVED_FIELDS,
    ]

    missing_columns = [
        col for col in required if col not in df.columns
    ]

    if missing_columns:
        raise SystemExit(
            f"Missing required columns: {missing_columns}"
        )

    print(f"\nTotal archived rows: {len(df)}")

    # Validate the latest model initialization cycle.
    df["initialization_time_utc"] = pd.to_datetime(
        df["initialization_time_utc"],
        utc=True,
        errors="coerce",
    )

    df["forecast_hour"] = pd.to_numeric(
        df["forecast_hour"], errors="coerce"
    )

    if df["initialization_time_utc"].isna().any():
        raise SystemExit("Invalid initialization timestamps found.")

    latest_cycle = df["initialization_time_utc"].max()

    current = df[
        df["initialization_time_utc"] == latest_cycle
    ].copy()

    print(f"Latest HRRR cycle: {latest_cycle}")
    print(f"Rows in latest cycle: {len(current)}")

    # 1. Location coverage
    station_ids = set(current["station_id"].dropna().astype(str))
    location_count = len(station_ids)

    report(
        "167 locations present",
        location_count == EXPECTED_LOCATIONS,
        f"Found {location_count} locations."
    )

    # 2. Original six locations
    missing_cities = CITY_IDS - station_ids

    report(
        "Original six Southern California locations",
        not missing_cities,
        f"Missing: {sorted(missing_cities) if missing_cities else 'None'}"
    )

    # 3. Forecast-hour coverage
    actual_hours = set(
        current["forecast_hour"].dropna().astype(int)
    )

    missing_hours = EXPECTED_HOURS - actual_hours

    report(
        "All six forecast hours",
        actual_hours == EXPECTED_HOURS,
        f"Found: {sorted(actual_hours)}"
    )

    # 4. Six records per location
    records_per_station = current.groupby("station_id").size()

    incorrect_counts = records_per_station[
        records_per_station != len(EXPECTED_HOURS)
    ]

    report(
        "Six records per location",
        len(incorrect_counts) == 0
        and len(records_per_station) == EXPECTED_LOCATIONS,
        f"Locations with incorrect counts: {len(incorrect_counts)}"
    )

    # 5. Duplicate records
    duplicate_key = [
        "station_id",
        "initialization_time_utc",
        "forecast_hour",
    ]

    duplicates = current.duplicated(
        subset=duplicate_key, keep=False
    ).sum()

    report(
        "No duplicate station/forecast records",
        duplicates == 0,
        f"Duplicate rows: {duplicates}"
    )

    # 6. Verify all weather variables
    print("\nWEATHER VARIABLE COMPLETENESS")
    print("-" * 65)

    for field in WEATHER_FIELDS + DERIVED_FIELDS:
        values = pd.to_numeric(
            current[field], errors="coerce"
        )

        valid = int(
            np.isfinite(values.to_numpy(dtype=float)).sum()
        )

        report(
            field,
            valid == len(current),
            f"{valid}/{len(current)} valid values"
        )

    # 7. Compare with the station catalog
    if CATALOG_FILE.exists():
        catalog = pd.read_csv(CATALOG_FILE)

        catalog_ids = set(
            catalog["station_id"].dropna().astype(str)
        )

        expected_ids = catalog_ids | CITY_IDS

        missing_stations = expected_ids - station_ids
        unexpected_stations = station_ids - expected_ids

        report(
            "Catalog station coverage",
            not missing_stations and not unexpected_stations,
            f"Missing: {len(missing_stations)}; "
            f"Unexpected: {len(unexpected_stations)}"
        )
    else:
        print("\n[WARNING] IEM station catalog not found.")

    # 8. Basic physical plausibility checks
    print("\nBASIC WEATHER QUALITY CHECKS")
    print("-" * 65)

    ranges = {
        "temperature_2m_k": (180, 340),
        "dew_point_2m_k": (170, 330),
        "relative_humidity_2m_pct": (0, 100),
        "wind_gust_ms": (0, 100),
        "precipitation_accum_kg_m2": (0, None),
        "shortwave_radiation_w_m2": (0, 1500),
        "surface_pressure_pa": (50000, 110000),
        "wind_speed_10m_ms": (0, 100),
        "wind_direction_10m_deg": (0, 360),
    }

    for field, (low, high) in ranges.items():
        values = pd.to_numeric(
            current[field], errors="coerce"
        )

        invalid = values.isna()

        if low is not None:
            invalid |= values < low

        if high is not None:
            invalid |= values > high

        report(
            f"{field} plausible range",
            int(invalid.sum()) == 0,
            f"Values outside configured range: {int(invalid.sum())}"
        )

    # 9. Final summary
    print("\n" + "=" * 65)
    print("VALIDATION SUMMARY")
    print("=" * 65)

    print(f"Latest cycle: {latest_cycle}")
    print(f"Locations: {location_count}/{EXPECTED_LOCATIONS}")
    print(f"Forecast hours: {sorted(actual_hours)}")
    print(f"Expected records: {EXPECTED_LOCATIONS * len(EXPECTED_HOURS)}")
    print(f"Actual records: {len(current)}")
    print(f"Duplicate records: {duplicates}")

    print("\nValidation complete.")
    print("Review all FAIL and WARNING messages before integration.")


if __name__ == "__main__":
    main()
