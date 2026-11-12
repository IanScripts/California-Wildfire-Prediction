"""
Extract NOAA HRRR forecast fields at California station coordinates.

Uses Herbie to access NOAA's public HRRR GRIB2 archive.

Examples:
  python noaa_hrrr_california.py --catalog-only
  python noaa_hrrr_california.py --max-stations 9 --forecast-hours 0 1 2 3 4 5
  python noaa_hrrr_california.py --forecast-hours 0 1 2 3 4 5
  python noaa_hrrr_california.py --cycle 2026-11-05T12 --forecast-hours 0 1 2 3 4 5

All six original Southern California locations are retained.
California IEM ASOS station locations are added.

Historical forecast cycles are preserved.
Missing weather values are never fabricated.
Precipitation accumulation metadata is retained.
"""
from __future__ import annotations

import argparse
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent

CATALOG = (
    ROOT / "live" / "california_sources" / "iem"
    / "iem_california_station_catalog.csv"
)

OUT_DIR = ROOT / "live" / "california_sources" / "noaa_hrrr"
OUT_FILE = OUT_DIR / "noaa_hrrr_california_forecast.csv"
FAILURE_FILE = OUT_DIR / "hrrr_failures.csv"
QUALITY_FILE = OUT_DIR / "hrrr_quality_checks.csv"
CACHE_DIR = OUT_DIR / "grib_cache"

SIX_SOUTHERN_CALIFORNIA = {
    "Los Angeles": (34.0522, -118.2437),
    "Ventura": (34.2805, -119.2945),
    "Riverside": (33.9806, -117.3755),
    "San Bernardino": (34.1083, -117.2898),
    "Santa Ana": (33.7455, -117.8677),
    "San Diego": (32.7157, -117.1611),
}

FIELDS = {
    "temperature_2m_k": ":TMP:2 m",
    "dew_point_2m_k": ":DPT:2 m",
    "relative_humidity_2m_pct": ":RH:2 m",
    "u_wind_10m_ms": ":UGRD:10 m",
    "v_wind_10m_ms": ":VGRD:10 m",
    "wind_gust_ms": ":GUST:",
    "precipitation_accum_kg_m2": ":APCP:surface",
    "shortwave_radiation_w_m2": ":DSWRF:surface",
    "surface_pressure_pa": ":PRES:surface",
}

RECORD_KEY = [
    "station_id",
    "initialization_time_utc",
    "forecast_hour",
]

PLAUSIBILITY_RANGES = {
    "temperature_2m_k": (180.0, 340.0),
    "dew_point_2m_k": (170.0, 330.0),
    "relative_humidity_2m_pct": (0.0, 100.0),
    "u_wind_10m_ms": (-100.0, 100.0),
    "v_wind_10m_ms": (-100.0, 100.0),
    "wind_gust_ms": (0.0, 150.0),
    "precipitation_accum_kg_m2": (0.0, 1000.0),
    "shortwave_radiation_w_m2": (0.0, 1500.0),
    "surface_pressure_pa": (50000.0, 110000.0),
}


def normalize_utc(value: str) -> datetime:
    """Parse an ISO datetime and return timezone-aware UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def load_catalog(max_stations: int | None = None) -> pd.DataFrame:
    """Combine six original city points with all IEM catalog stations."""

    if not CATALOG.is_file():
        raise FileNotFoundError(
            f"{CATALOG} not found. Run this first:\n"
            "python iem_california.py --catalog-only"
        )

    station_df = pd.read_csv(CATALOG)

    required = [
        "station_id",
        "station_name",
        "latitude",
        "longitude",
    ]

    missing = [
        column for column in required
        if column not in station_df.columns
    ]

    if missing:
        raise ValueError(
            f"IEM catalog missing columns: {missing}"
        )

    city_rows = [
        {
            "station_id": "CITY_" + name.lower().replace(" ", "_"),
            "network": "PROJECT_CITY",
            "station_name": name,
            "latitude": lat,
            "longitude": lon,
            "location_type": "original_socal_location",
        }
        for name, (lat, lon) in SIX_SOUTHERN_CALIFORNIA.items()
    ]

    station_df = station_df.copy()
    station_df["location_type"] = "iem_asos_station"

    combined = pd.concat(
        [pd.DataFrame(city_rows), station_df],
        ignore_index=True,
    )

    combined["latitude"] = pd.to_numeric(
        combined["latitude"], errors="coerce"
    )

    combined["longitude"] = pd.to_numeric(
        combined["longitude"], errors="coerce"
    )

    combined = combined.dropna(
        subset=["latitude", "longitude"]
    )

    combined = combined[
        combined["latitude"].between(-90, 90)
        & combined["longitude"].between(-180, 180)
    ]

    combined = combined.drop_duplicates(
        subset=["station_id"],
        keep="first",
    )

    combined = combined.reset_index(drop=True)

    if max_stations is not None:
        combined = combined.head(max_stations)

    return combined


def haversine_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Calculate great-circle distance between coordinates."""

    radius_km = 6371.0088

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dl / 2) ** 2
    )

    return 2 * radius_km * math.asin(
        min(1.0, math.sqrt(a))
    )


def nearest_grid_index(ds, latitude: float, longitude: float):
    """Find nearest HRRR grid cell for 1-D or 2-D coordinates."""

    lat_name = next(
        (
            name for name in ("latitude", "lat")
            if name in ds.coords or name in ds
        ),
        None,
    )

    lon_name = next(
        (
            name for name in ("longitude", "lon")
            if name in ds.coords or name in ds
        ),
        None,
    )

    if lat_name is None or lon_name is None:
        raise ValueError(
            "HRRR dataset does not expose latitude/longitude coordinates."
        )

    lat_var = ds[lat_name]
    lon_var = ds[lon_name]

    lats = np.asarray(lat_var.values)
    lons = np.asarray(lon_var.values)

    lons = ((lons + 180.0) % 360.0) - 180.0

    if lats.ndim == 1 and lons.ndim == 1:

        if lat_var.ndim != 1 or lon_var.ndim != 1:
            raise ValueError(
                "Unexpected one-dimensional coordinate dimensions."
            )

        lon_grid, lat_grid = np.meshgrid(lons, lats)

        ydim = lat_var.dims[0]
        xdim = lon_var.dims[0]

    elif (
        lats.ndim == 2
        and lons.ndim == 2
        and lats.shape == lons.shape
    ):

        if len(lat_var.dims) != 2 or len(lon_var.dims) != 2:
            raise ValueError(
                "Unexpected two-dimensional coordinate dimensions."
            )

        if lat_var.dims != lon_var.dims:
            raise ValueError(
                "HRRR latitude/longitude dimensions do not match."
            )

        lat_grid = lats
        lon_grid = lons

        ydim, xdim = lat_var.dims

    else:
        raise ValueError(
            f"Unsupported coordinate shapes: "
            f"lat={lats.shape}, lon={lons.shape}"
        )

    dlat = lat_grid - latitude

    dlon = (
        (lon_grid - longitude + 180.0) % 360.0 - 180.0
    ) * math.cos(math.radians(latitude))

    distance_squared = dlat**2 + dlon**2

    valid = (
        np.isfinite(distance_squared)
        & np.isfinite(lat_grid)
        & np.isfinite(lon_grid)
    )

    if not valid.any():
        raise ValueError(
            "No valid HRRR grid coordinates found."
        )

    distance_squared = np.where(
        valid,
        distance_squared,
        np.inf,
    )

    iy, ix = np.unravel_index(
        np.argmin(distance_squared),
        distance_squared.shape,
    )

    return (
        {
            ydim: int(iy),
            xdim: int(ix),
        },
        float(lat_grid[iy, ix]),
        float(lon_grid[iy, ix]),
    )


def scalar_at_point(ds, latitude: float, longitude: float):
    """Extract exactly one numeric value at the nearest grid cell."""

    indexers, grid_lat, grid_lon = nearest_grid_index(
        ds, latitude, longitude
    )

    candidates = []

    for variable_name in ds.data_vars:

        try:
            selected = ds[variable_name].isel(
                indexers
            ).squeeze(drop=True)

            values = np.asarray(selected.values)

            if values.size != 1:
                continue

            if not np.issubdtype(values.dtype, np.number):
                continue

            value = float(values.reshape(-1)[0])

            if np.isfinite(value):
                candidates.append(
                    (
                        variable_name,
                        value,
                        selected.attrs,
                    )
                )

        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ):
            continue

    if len(candidates) != 1:
        raise ValueError(
            "Expected exactly one scalar meteorological variable "
            f"at the grid cell; found {len(candidates)}."
        )

    variable_name, value, attrs = candidates[0]

    return (
        value,
        grid_lat,
        grid_lon,
        variable_name,
        attrs,
    )


def metadata_value(attrs: dict, *names: str):
    """Return available GRIB metadata without guessing."""

    for name in names:
        value = attrs.get(name)

        if value is not None:
            return str(value)

    return None

def parse_step_range(step_range: str | None):
    """Parse a GRIB step range such as '0-1'."""

    if not step_range:
        return None, None

    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*",
        step_range,
    )

    if not match:
        return None, None

    return match.group(1), match.group(2)


def get_cycle(value: str | None = None):
    """Return aware UTC for records and naive UTC for Herbie."""

    if value:
        cycle_utc = normalize_utc(value)

    else:
        cycle_utc = (
            datetime.now(timezone.utc)
            .replace(
                minute=0,
                second=0,
                microsecond=0,
            )
            - timedelta(hours=2)
        )

    return (
        cycle_utc,
        cycle_utc.replace(tzinfo=None),
    )


def upsert_forecast_archive(new_frame: pd.DataFrame) -> int:
    """
    Preserve historical forecasts.

    Existing records with matching station, initialization time,
    and forecast hour are replaced by the latest extraction.
    """

    if OUT_FILE.exists():

        old_frame = pd.read_csv(
            OUT_FILE,
            low_memory=False,
        )

        missing_keys = [
            key for key in RECORD_KEY
            if key not in old_frame.columns
        ]

        if missing_keys:
            raise ValueError(
                f"Existing forecast archive lacks key columns "
                f"{missing_keys}. Back it up before starting a new archive."
            )

        combined = pd.concat(
            [old_frame, new_frame],
            ignore_index=True,
            sort=False,
        )

    else:
        combined = new_frame.copy()

    before = len(combined)

    combined = combined.drop_duplicates(
        subset=RECORD_KEY,
        keep="last",
    )

    combined = combined.sort_values(
        RECORD_KEY
    ).reset_index(drop=True)

    OUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = OUT_FILE.with_suffix(".tmp.csv")

    combined.to_csv(
        temp_file,
        index=False,
    )

    temp_file.replace(OUT_FILE)

    return before - len(combined)


def validate_run(
    frame: pd.DataFrame,
    stations: pd.DataFrame,
    forecast_hours: list[int],
):
    """
    Generate non-destructive data-quality warnings.

    Values outside plausibility ranges are retained.
    """

    issues = []

    expected = len(stations) * len(forecast_hours)

    if len(frame) != expected:
        issues.append({
            "check": "record_count",
            "severity": "WARNING",
            "details": (
                f"Expected {expected} station-hour rows; "
                f"collected {len(frame)}."
            ),
        })

    if not frame.empty:

        duplicate_mask = frame.duplicated(
            RECORD_KEY,
            keep=False,
        )

        if duplicate_mask.any():
            issues.append({
                "check": "duplicate_records",
                "severity": "WARNING",
                "details": (
                    f"{int(duplicate_mask.sum())} rows share "
                    "a station/cycle/forecast-hour key."
                ),
            })

        actual_stations = set(
            frame["station_id"].astype(str)
        )

        expected_stations = set(
            stations["station_id"].astype(str)
        )

        missing_stations = sorted(
            expected_stations - actual_stations
        )

        if missing_stations:
            issues.append({
                "check": "station_coverage",
                "severity": "WARNING",
                "details": (
                    "No records collected for: "
                    + ", ".join(missing_stations)
                ),
            })

        expected_pairs = {
            (str(station_id), int(hour))
            for station_id in expected_stations
            for hour in forecast_hours
        }

        actual_pairs = {
            (
                str(row.station_id),
                int(row.forecast_hour),
            )
            for row in frame[
                ["station_id", "forecast_hour"]
            ].itertuples(index=False)
        }

        missing_pairs = expected_pairs - actual_pairs

        if missing_pairs:
            issues.append({
                "check": "station_forecast_hour_coverage",
                "severity": "WARNING",
                "details": (
                    f"{len(missing_pairs)} expected station/"
                    "forecast-hour combinations are missing. "
                    "Check the extraction failure report."
                ),
            })

        if "grid_coordinate_consistent" in frame.columns:

            inconsistent = (
                frame["grid_coordinate_consistent"].eq(False)
            )

            if inconsistent.any():
                issues.append({
                    "check": "grid_coordinate_consistency",
                    "severity": "WARNING",
                    "details": (
                        f"{int(inconsistent.sum())} station-hour "
                        "rows used differing nearest grid coordinates."
                    ),
                })

        for field, (minimum, maximum) in PLAUSIBILITY_RANGES.items():

            if field not in frame.columns:
                continue

            values = pd.to_numeric(
                frame[field],
                errors="coerce",
            )

            invalid = (
                values.notna()
                & ~values.between(minimum, maximum)
            )

            if invalid.any():
                issues.append({
                    "check": f"plausibility_{field}",
                    "severity": "WARNING",
                    "details": (
                        f"{int(invalid.sum())} values outside "
                        f"the review range [{minimum}, {maximum}]. "
                        "Values were retained."
                    ),
                })

        coordinate_columns = {
            "station_latitude",
            "station_longitude",
            "grid_latitude",
            "grid_longitude",
        }

        if coordinate_columns.issubset(frame.columns):

            distances = frame.apply(
                lambda row: haversine_km(
                    float(row["station_latitude"]),
                    float(row["station_longitude"]),
                    float(row["grid_latitude"]),
                    float(row["grid_longitude"]),
                ),
                axis=1,
            )

            far = distances > 10.0

            if far.any():
                issues.append({
                    "check": "nearest_grid_distance",
                    "severity": "WARNING",
                    "details": (
                        f"{int(far.sum())} rows have a nearest "
                        "HRRR grid point more than 10 km away. "
                        "Review station/grid coverage."
                    ),
                })

    return issues

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Extract NOAA HRRR forecasts for California "
            "locations and IEM stations."
        )
    )

    parser.add_argument(
        "--catalog-only",
        action="store_true",
        help="Only load and report available locations.",
    )

    parser.add_argument(
        "--cycle",
        default=None,
        help="UTC initialization time: YYYY-MM-DDTHH[Z].",
    )

    parser.add_argument(
        "--forecast-hours",
        nargs="+",
        type=int,
        default=list(range(0, 19)),
        help="Forecast lead hours (0 through 48).",
    )

    parser.add_argument(
        "--max-stations",
        type=int,
        default=None,
        help="Optional location limit for testing.",
    )

    args = parser.parse_args()

    if args.max_stations is not None and args.max_stations < 1:
        parser.error("--max-stations must be at least 1")

    if not args.forecast_hours:
        parser.error("At least one forecast hour is required.")

    invalid_hours = sorted(
        set(
            h for h in args.forecast_hours
            if h < 0 or h > 48
        )
    )

    if invalid_hours:
        parser.error(
            f"Forecast hours must be between 0 and 48: {invalid_hours}"
        )

    try:
        stations = load_catalog(args.max_stations)

    except Exception as exc:
        raise SystemExit(
            f"Could not load station catalog: {exc}"
        ) from exc

    print("\n" + "=" * 60)
    print("NOAA HRRR CALIFORNIA FORECAST COLLECTION")
    print("=" * 60)

    print(f"Locations selected: {len(stations)}")

    print(
        "Original Southern California locations: "
        f"{int((stations.location_type == 'original_socal_location').sum())}"
    )

    print(
        "IEM stations selected: "
        f"{int((stations.location_type == 'iem_asos_station').sum())}"
    )

    if args.catalog_only:

        print("\nCatalog preview:")

        print(
            stations[
                [
                    "station_id",
                    "station_name",
                    "latitude",
                    "longitude",
                    "location_type",
                ]
            ].to_string(index=False)
        )

        return

    try:
        from herbie import Herbie

    except ImportError as exc:
        raise SystemExit(
            "Missing HRRR dependencies. Run:\n"
            "python -m pip install -r requirements_data_sources.txt"
        ) from exc

    OUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    CACHE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    cycle_utc, cycle_herbie = get_cycle(args.cycle)

    forecast_hours = sorted(
        set(args.forecast_hours)
    )

    rows = []
    failures = []

    for fxx in forecast_hours:

        print(
            f"\nHRRR run {cycle_utc:%Y-%m-%d %H}Z, "
            f"forecast hour f{fxx:02d}"
        )

        try:
            h = Herbie(
                cycle_herbie,
                model="hrrr",
                product="sfc",
                fxx=fxx,
                save_dir=str(CACHE_DIR),
                overwrite=False,
            )

        except Exception as exc:

            failures.append({
                "forecast_hour": fxx,
                "station_or_field": "HRRR initialization",
                "error": str(exc),
            })

            print(
                f"  Could not initialize HRRR run: {exc}"
            )

            continue

        extracted = {}

        for output_name, search_string in FIELDS.items():

            try:
                # Current Herbie API uses search, not searchString.
                ds = h.xarray(
                    search=search_string
                )

                extracted[output_name] = ds

                print(
                    f"  Retrieved field: {output_name}"
                )

            except Exception as exc:

                failures.append({
                    "forecast_hour": fxx,
                    "station_or_field": output_name,
                    "error": str(exc),
                })

                print(
                    f"  Field unavailable: {output_name}: {exc}"
                )

        if not extracted:

            print("  No meteorological fields extracted.")
            continue

        for station in stations.to_dict("records"):

            base = {
                "station_id": str(station["station_id"]),
                "network": station.get("network"),
                "station_name": station.get("station_name"),
                "location_type": station.get("location_type"),
                "station_latitude": float(station["latitude"]),
                "station_longitude": float(station["longitude"]),
                "model": "NOAA HRRR",
                "source": "NOAA HRRR public GRIB2 archive via Herbie",
                "data_type": "forecast_model",
                "initialization_time_utc": cycle_utc.isoformat(),
                "forecast_hour": fxx,
                "valid_time_utc": (
                    cycle_utc + timedelta(hours=fxx)
                ).isoformat(),
                "retrieved_at_utc": datetime.now(
                    timezone.utc
                ).isoformat(timespec="seconds"),
            }

            grid_locations = []

            for output_name, ds in extracted.items():

                try:
                    (
                        value,
                        grid_lat,
                        grid_lon,
                        variable_name,
                        attrs,
                    ) = scalar_at_point(
                        ds,
                        base["station_latitude"],
                        base["station_longitude"],
                    )

                    base[output_name] = value

                    grid_locations.append(
                        (grid_lat, grid_lon)
                    )

                    # Preserve precipitation accumulation metadata.
                    if output_name == "precipitation_accum_kg_m2":

                        step_range = metadata_value(
                            attrs,
                            "GRIB_stepRange",
                            "stepRange",
                        )

                        step_start, step_end = parse_step_range(
                            step_range
                        )

                        base["precipitation_grib_variable"] = (
                            variable_name
                        )

                        base["precipitation_units"] = metadata_value(
                            attrs,
                            "GRIB_units",
                            "units",
                        )

                        base["precipitation_step_type"] = metadata_value(
                            attrs,
                            "GRIB_stepType",
                            "stepType",
                        )

                        base["precipitation_step_range"] = (
                            step_range
                        )

                        base["precipitation_start_step"] = (
                            metadata_value(
                                attrs,
                                "GRIB_startStep",
                                "startStep",
                            )
                            or step_start
                        )

                        base["precipitation_end_step"] = (
                            metadata_value(
                                attrs,
                                "GRIB_endStep",
                                "endStep",
                            )
                            or step_end
                        )

                        base["precipitation_step_units"] = (
                            metadata_value(
                                attrs,
                                "GRIB_stepUnits",
                                "stepUnits",
                            )
                        )

                except Exception as exc:

                    failures.append({
                        "forecast_hour": fxx,
                        "station_or_field": (
                            f"{station['station_id']}/{output_name}"
                        ),
                        "error": str(exc),
                    })

            if grid_locations:

                base["grid_latitude"] = grid_locations[0][0]
                base["grid_longitude"] = grid_locations[0][1]

                base["grid_coordinate_consistent"] = all(
                    abs(lat - grid_locations[0][0]) < 1e-6
                    and abs(lon - grid_locations[0][1]) < 1e-6
                    for lat, lon in grid_locations[1:]
                )

            # Calculate wind speed and direction from U/V components.
            u = base.get("u_wind_10m_ms")
            v = base.get("v_wind_10m_ms")

            if u is not None and v is not None:

                base["wind_speed_10m_ms"] = float(
                    np.hypot(u, v)
                )

                base["wind_direction_10m_deg"] = float(
                    (
                        270.0
                        - np.degrees(np.arctan2(v, u))
                    ) % 360.0
                )

            rows.append(base)

        # Close datasets to release memory.
        for ds in extracted.values():

            try:
                ds.close()

            except Exception:
                pass

        print(
            f"  Processed locations: {len(stations)}"
        )

    if rows:

        frame = pd.DataFrame(rows)

        quality_issues = validate_run(
            frame,
            stations,
            forecast_hours,
        )

        duplicate_count = upsert_forecast_archive(
            frame
        )

        print("\n" + "=" * 60)
        print("HRRR COLLECTION SUMMARY")
        print("=" * 60)

        print(f"Saved/updated: {OUT_FILE}")
        print(f"Rows collected in this run: {len(frame)}")

        print(
            f"Duplicate archive rows replaced: {duplicate_count}"
        )

        print(
            f"Unique locations in this run: "
            f"{frame.station_id.nunique()}"
        )

        print(
            f"Forecast hours requested: {len(forecast_hours)}"
        )

        print("\nAvailable weather fields:")

        for field in FIELDS:

            if field in frame.columns:

                print(
                    f"  {field}: "
                    f"{frame[field].notna().sum()} valid values"
                )

        print("\nDerived wind fields:")

        for field in (
            "wind_speed_10m_ms",
            "wind_direction_10m_deg",
        ):

            if field in frame.columns:

                print(
                    f"  {field}: "
                    f"{frame[field].notna().sum()} valid values"
                )

        if quality_issues:

            pd.DataFrame(
                quality_issues
            ).to_csv(
                QUALITY_FILE,
                index=False,
            )

            print(
                f"\nQuality warnings: {len(quality_issues)}"
            )

            print(
                f"Quality report: {QUALITY_FILE}"
            )

            for issue in quality_issues:

                print(
                    f"  {issue['check']}: {issue['details']}"
                )

        else:

            if QUALITY_FILE.exists():
                QUALITY_FILE.unlink()

            print(
                "\nNo quality warnings from configured checks."
            )

    else:

        print(
            "\nNo HRRR station data extracted."
        )

    # Save failures, or remove stale failure reports.
    if failures:

        pd.DataFrame(
            failures
        ).to_csv(
            FAILURE_FILE,
            index=False,
        )

        print(
            f"\nWarnings/failures recorded: {len(failures)}"
        )

        print(
            f"Failure report: {FAILURE_FILE}"
        )

    else:

        if FAILURE_FILE.exists():
            FAILURE_FILE.unlink()

        print(
            "\nNo extraction failures reported."
        )

    if failures or not rows:
        raise SystemExit(1)


if __name__ == "__main__":
    main()