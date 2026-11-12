
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent

DATA_FILE = (
    ROOT / "live" / "california_sources" /
    "iem" / "iem_california_observations.csv"
)

CATALOG_FILE = (
    ROOT / "live" / "california_sources" /
    "iem" / "iem_california_station_catalog.csv"
)

REPORT_DIR = (
    ROOT / "live" / "california_sources" /
    "iem" / "validation"
)

WEATHER_FIELDS = [
    "tmpf",       # Temperature (F)
    "dwpf",       # Dew point (F)
    "relh",       # Relative humidity (%)
    "drct",       # Wind direction (degrees)
    "sknt",       # Wind speed (knots)
    "gust",       # Wind gust (knots)
    "p01i",       # Precipitation (inches)
    "alti",       # Altimeter setting
    "mslp",       # Sea-level pressure
    "vsby",       # Visibility
    "feel",       # Apparent temperature
]


def report(label, passed, details=""):
    status = "PASS" if passed else "CHECK"
    print(f"[{status}] {label}")
    if details:
        print(f"       {details}")


def main():
    print("=" * 65)
    print("IEM CALIFORNIA HISTORICAL DATA VALIDATION")
    print("=" * 65)

    if not DATA_FILE.exists():
        raise SystemExit(f"Missing observation file: {DATA_FILE}")

    if not CATALOG_FILE.exists():
        raise SystemExit(f"Missing station catalog: {CATALOG_FILE}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(DATA_FILE, low_memory=False)
    catalog = pd.read_csv(CATALOG_FILE)

    print(f"\nObservation rows: {len(df)}")
    print(f"Catalog stations: {len(catalog)}")

    # Identify station ID column.
    if "station_id" in df.columns:
        station_col = "station_id"
    elif "station" in df.columns:
        station_col = "station"
    else:
        raise SystemExit("No station ID column found.")

    if "valid" not in df.columns:
        raise SystemExit("Missing observation timestamp column: valid")

    df[station_col] = df[station_col].astype("string").str.strip()

    # Parse timestamps.
    df["parsed_time"] = pd.to_datetime(
        df["valid"], utc=True, errors="coerce"
    )

    invalid_times = int(df["parsed_time"].isna().sum())
    df = df.dropna(subset=["parsed_time", station_col]).copy()

    df["year"] = df["parsed_time"].dt.year
    df["date"] = df["parsed_time"].dt.date

    station_ids = set(df[station_col].astype(str))
    catalog_ids = set(catalog["station_id"].astype(str))

    # 1. Station coverage.
    missing_stations = catalog_ids - station_ids
    unexpected_stations = station_ids - catalog_ids

    print("\nSTATION COVERAGE")
    print("-" * 65)

    report(
        "California catalog station coverage",
        len(station_ids) == len(catalog_ids)
        and not missing_stations,
        f"Observed: {len(station_ids)} / {len(catalog_ids)}; "
        f"Missing: {len(missing_stations)}"
    )

    if missing_stations:
        print("Missing station IDs:", sorted(missing_stations)[:30])

    if unexpected_stations:
        print("Unexpected station IDs:", sorted(unexpected_stations)[:30])

    # 2. Overall historical date coverage.
    print("\nHISTORICAL DATE COVERAGE")
    print("-" * 65)

    first_date = df["parsed_time"].min()
    last_date = df["parsed_time"].max()

    print(f"First observation: {first_date}")
    print(f"Last observation:  {last_date}")
    print(f"Years represented: {df['year'].nunique()}")
    print(f"Distinct observation days: {df['date'].nunique()}")

    # 3. Duplicate station/timestamp records.
    duplicates = int(
        df.duplicated(
            subset=[station_col, "parsed_time"],
            keep=False
        ).sum()
    )

    report(
        "No duplicate station/timestamp records",
        duplicates == 0,
        f"Duplicate rows: {duplicates}"
    )

    # 4. Timestamp quality.
    report(
        "All timestamps successfully parsed",
        invalid_times == 0,
        f"Invalid timestamps: {invalid_times}"
    )

    future_count = int(
        (df["parsed_time"] >
         pd.Timestamp.now(tz="UTC") +
         pd.Timedelta(days=1)).sum()
    )

    report(
        "No unexpectedly future observations",
        future_count == 0,
        f"Future rows: {future_count}"
    )

    # 5. Weather variable completeness.
    print("\nWEATHER VARIABLE COMPLETENESS")
    print("-" * 65)

    missingness = []

    for field in WEATHER_FIELDS:
        if field not in df.columns:
            missingness.append({
                "field": field,
                "valid_values": 0,
                "missing_values": len(df),
                "valid_percent": 0.0,
                "status": "COLUMN MISSING",
            })

            report(field, False, "Column not found")
            continue

        values = pd.to_numeric(df[field], errors="coerce")
        valid = int(np.isfinite(values.to_numpy()).sum())
        missing = len(df) - valid
        percentage = (
            100 * valid / len(df) if len(df) else 0
        )

        missingness.append({
            "field": field,
            "valid_values": valid,
            "missing_values": missing,
            "valid_percent": round(percentage, 2),
            "status": "AVAILABLE" if valid else "NO DATA",
        })

        report(
            field,
            valid > 0,
            f"{valid} valid / {missing} missing "
            f"({percentage:.2f}% available)"
        )

    pd.DataFrame(missingness).to_csv(
        REPORT_DIR / "iem_variable_completeness.csv",
        index=False
    )

    # 6. Per-station coverage.
    station_stats = (
        df.groupby(station_col)
        .agg(
            observations=("parsed_time", "count"),
            first_observation=("parsed_time", "min"),
            last_observation=("parsed_time", "max"),
            distinct_days=("date", "nunique"),
            years_available=("year", "nunique"),
        )
        .reset_index()
        .rename(columns={station_col: "station_id"})
    )

    station_stats = catalog.merge(
        station_stats,
        on="station_id",
        how="left"
    )

    station_stats["observations"] = (
        station_stats["observations"].fillna(0).astype(int)
    )

    station_stats.to_csv(
        REPORT_DIR / "iem_station_coverage.csv",
        index=False
    )

    stations_with_data = int(
        (station_stats["observations"] > 0).sum()
    )

    print("\nPER-STATION COVERAGE")
    print("-" * 65)

    report(
        "Stations with observations",
        stations_with_data == len(catalog),
        f"{stations_with_data}/{len(catalog)} stations"
    )

    print("\nStations with most observations:")
    print(
        station_stats.nlargest(10, "observations")[
            ["station_id", "station_name", "observations"]
        ].to_string(index=False)
    )

    # 7. Station-year coverage.
    station_year = (
        df.groupby([station_col, "year"])
        .size()
        .reset_index(name="observations")
        .rename(columns={station_col: "station_id"})
    )

    station_year.to_csv(
        REPORT_DIR / "iem_station_year_coverage.csv",
        index=False
    )

    # 8. Basic physical plausibility checks.
    print("\nBASIC WEATHER QUALITY CHECKS")
    print("-" * 65)

    ranges = {
        "tmpf": (-80, 140),
        "dwpf": (-100, 100),
        "relh": (0, 100),
        "drct": (0, 360),
        "sknt": (0, 200),
        "gust": (0, 250),
        "p01i": (0, None),
    }

    for field, (low, high) in ranges.items():
        if field not in df.columns:
            continue

        values = pd.to_numeric(df[field], errors="coerce")

        invalid = values.notna() & (
            (values < low) if low is not None
            else False
        )

        if high is not None:
            invalid |= values.notna() & (values > high)

        report(
            f"{field} plausible range",
            int(invalid.sum()) == 0,
            f"Out-of-range values: {int(invalid.sum())}"
        )

    # 9. Final summary.
    print("\n" + "=" * 65)
    print("IEM VALIDATION SUMMARY")
    print("=" * 65)

    print(f"Total observation rows: {len(df)}")
    print(f"Catalog stations: {len(catalog)}")
    print(f"Stations with data: {stations_with_data}")
    print(f"First observation: {first_date}")
    print(f"Last observation: {last_date}")
    print(f"Distinct years: {df['year'].nunique()}")
    print(f"Duplicate rows: {duplicates}")
    print(f"Invalid timestamps: {invalid_times}")

    print("\nREPORT FILES:")
    print(REPORT_DIR)

    print("\nValidation finished.")
    print("CHECK results require review before statewide integration.")


if __name__ == "__main__":
    main()
