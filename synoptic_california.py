
"""
Synoptic California Weather Observation Collector

Purpose:
- Discover active Synoptic stations near the original six SoCal locations.
- Retrieve actual historical weather observations.
- Preserve all available observation variables.
- Store data separately from IEM, Open-Meteo, and HRRR.
- Use manageable batches to avoid excessive API requests.

Requirements:
    pip install requests pandas

Environment:
    SYNOPTIC_TOKEN must be configured.

Example:
    python synoptic_california.py --catalog-only

    python synoptic_california.py --start-date 2026-10-01 --end-date 2026-11-05

    python synoptic_california.py --start-date 2026-10-01 --end-date 2026-11-05 --radius 25
"""

import os
import csv
import time
import argparse
from pathlib import Path
from datetime import datetime, date, timedelta, timezone

import requests


# --------------------------------------------------
# PROJECT PATHS
# --------------------------------------------------

ROOT = Path(__file__).resolve().parent

OUT_DIR = ROOT / "live" / "california_sources" / "synoptic"

CATALOG_FILE = OUT_DIR / "synoptic_california_station_catalog.csv"

API_BASE = "https://api.synopticdata.com/v2/stations"

TOKEN = os.getenv("SYNOPTIC_TOKEN")

TIMEOUT = 120

# Six original project locations.
LOCATIONS = {
    "Los Angeles": (34.0522, -118.2437),
    "Ventura": (34.2805, -119.2945),
    "Riverside": (33.9806, -117.3755),
    "San Bernardino": (34.1083, -117.2898),
    "Santa Ana": (33.7455, -117.8677),
    "San Diego": (32.7157, -117.1611),
}


# --------------------------------------------------
# API REQUEST
# --------------------------------------------------

def api_request(endpoint, params):

    params["token"] = TOKEN

    response = requests.get(
        f"{API_BASE}/{endpoint}",
        params=params,
        timeout=TIMEOUT,
        headers={
            "User-Agent": "California-Wildfire-Research/1.0"
        }
    )

    response.raise_for_status()

    data = response.json()

    summary = data.get("SUMMARY", {})

    if summary.get("RESPONSE_CODE") != 1:
        raise RuntimeError(
            summary.get("RESPONSE_MESSAGE", "Synoptic API error")
        )

    return data


# --------------------------------------------------
# DISCOVER STATIONS
# --------------------------------------------------

def discover_stations(radius):

    stations = {}

    print("\nDISCOVERING SYNOPTIC STATIONS")
    print("=" * 60)

    for name, (lat, lon) in LOCATIONS.items():

        print(f"\nSearching: {name}")

        params = {
            "radius": f"{lat},{lon},{radius}",
            "status": "active",
            "complete": 1,
            "sensorvars": 1,
        }

        data = api_request("metadata", params)

        results = data.get("STATION", [])

        print(f"Stations discovered: {len(results)}")

        for station in results:

            station_id = station.get("STID")

            if not station_id:
                continue

            # Keep one copy of each station even if
            # it appears in multiple location searches.
            if station_id not in stations:

                stations[station_id] = {
                    "station_id": station_id,
                    "station_name": station.get("NAME"),
                    "latitude": station.get("LATITUDE"),
                    "longitude": station.get("LONGITUDE"),
                    "elevation": station.get("ELEVATION"),
                    "state": station.get("STATE"),
                    "network_id": station.get("MNET_ID"),
                    "status": station.get("STATUS"),
                    "period_of_record_start": (
                        station.get("PERIOD_OF_RECORD", {}).get("start")
                    ),
                    "period_of_record_end": (
                        station.get("PERIOD_OF_RECORD", {}).get("end")
                    ),
                    "nearby_project_locations": name,
                    "sensor_variables": str(
                        station.get("SENSOR_VARIABLES", {})
                    ),
                    "source": "Synoptic Data API",
                }

            else:
                previous = stations[station_id][
                    "nearby_project_locations"
                ]

                if name not in previous.split(";"):
                    stations[station_id][
                        "nearby_project_locations"
                    ] = previous + ";" + name

    if not stations:
        raise RuntimeError(
            "No Synoptic stations were discovered."
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    catalog = list(stations.values())

    with CATALOG_FILE.open(
        "w", newline="", encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=catalog[0].keys()
        )

        writer.writeheader()
        writer.writerows(catalog)

    print("\nSTATION DISCOVERY SUMMARY")
    print("=" * 60)
    print(f"Unique stations: {len(catalog)}")
    print(f"Saved catalog: {CATALOG_FILE}")

    return catalog


# --------------------------------------------------
# HISTORICAL DATE BATCHES
# --------------------------------------------------

def date_batches(start, end, days):

    current = start

    while current <= end:

        next_date = min(
            current + timedelta(days=days),
            end + timedelta(days=1)
        )

        yield (
            current,
            next_date - timedelta(minutes=1)
        )

        current = next_date


def api_time(value):

    return value.strftime("%Y%m%d%H%M")


# --------------------------------------------------
# FETCH WEATHER OBSERVATIONS
# --------------------------------------------------

def fetch_observations(
    catalog,
    start,
    end,
    batch_days=7,
    batch_stations=10
):

    output_file = OUT_DIR / (
        f"synoptic_observations_"
        f"{start:%Y%m%d}_{end:%Y%m%d}.csv"
    )

    if output_file.exists():
        raise FileExistsError(
            f"{output_file} already exists. "
            "Move it or use a different date range "
            "before rerunning to preserve existing data."
        )

    station_ids = [
        station["station_id"]
        for station in catalog
    ]

    station_lookup = {
        station["station_id"]: station
        for station in catalog
    }

    total_rows = 0
    successful_requests = 0
    failed_requests = 0

    print("\nSYNOPTIC HISTORICAL COLLECTION")
    print("=" * 60)

    print(f"Stations: {len(station_ids)}")
    print(f"Start: {start}")
    print(f"End: {end}")
    print(f"Batch days: {batch_days}")
    print(f"Stations per batch: {batch_stations}")

    # No vars filter: preserve all variables returned
    # by each selected station during the time window.

    with output_file.open(
        "w", newline="", encoding="utf-8"
    ) as file:

        writer = None

        for window_start, window_end in date_batches(
            start, end, batch_days
        ):

            print(
                f"\nDate window: {window_start} "
                f"to {window_end}"
            )

            for offset in range(
                0, len(station_ids), batch_stations
            ):

                batch = station_ids[
                    offset:offset + batch_stations
                ]

                print(
                    f"Requesting stations "
                    f"{offset + 1}-"
                    f"{offset + len(batch)} "
                    f"of {len(station_ids)}"
                )

                params = {
                    "stid": ",".join(batch),
                    "start": api_time(window_start),
                    "end": api_time(window_end),
                    "obtimezone": "UTC",
                    "complete": 1,
                    "sensorvars": 1,
                    "qc_checks": "synopticlabs,madis",
                }

                try:

                    data = api_request(
                        "timeseries",
                        params
                    )

                    stations = data.get("STATION", [])

                    for station in stations:

                        station_id = station.get("STID")

                        observations = station.get(
                            "OBSERVATIONS", {}
                        )

                        timestamps = observations.get(
                            "date_time", []
                        )

                        metadata = station_lookup.get(
                            station_id, {}
                        )

                        # Every variable array aligns
                        # with the timestamp array.
                        variables = {
                            key: values
                            for key, values in observations.items()
                            if key != "date_time"
                            and isinstance(values, list)
                        }

                        for index, timestamp in enumerate(
                            timestamps
                        ):

                            row = {
                                "station_id": station_id,
                                "station_name": station.get(
                                    "NAME"
                                ),
                                "latitude": station.get(
                                    "LATITUDE"
                                ),
                                "longitude": station.get(
                                    "LONGITUDE"
                                ),
                                "elevation": station.get(
                                    "ELEVATION"
                                ),
                                "network_id": station.get(
                                    "MNET_ID"
                                ),
                                "timestamp_utc": timestamp,
                                "source": "Synoptic Data API",
                                "data_type": "observed",
                                "nearby_project_locations": (
                                    metadata.get(
                                        "nearby_project_locations"
                                    )
                                ),
                            }

                            for variable, values in (
                                variables.items()
                            ):

                                if index < len(values):
                                    row[variable] = values[index]

                            if writer is None:

                                fieldnames = list(row.keys())

                                for variable in variables:
                                    if variable not in fieldnames:
                                        fieldnames.append(variable)

                                writer = csv.DictWriter(
                                    file,
                                    fieldnames=fieldnames,
                                    extrasaction="ignore"
                                )

                                writer.writeheader()

                            writer.writerow(row)
                            total_rows += 1

                    successful_requests += 1

                except Exception as error:

                    failed_requests += 1

                    print(
                        f"REQUEST FAILED: {error}"
                    )

                time.sleep(0.4)

    print("\nSYNOPTIC COLLECTION SUMMARY")
    print("=" * 60)
    print(f"Stations discovered: {len(station_ids)}")
    print(f"Successful requests: {successful_requests}")
    print(f"Failed requests: {failed_requests}")
    print(f"Observation rows: {total_rows}")
    print(f"Saved: {output_file}")

    return output_file, total_rows, failed_requests


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description="Collect Synoptic California weather observations."
    )

    parser.add_argument(
        "--catalog-only",
        action="store_true"
    )

    parser.add_argument(
        "--start-date",
        default=(date.today() - timedelta(days=7)).isoformat()
    )

    parser.add_argument(
        "--end-date",
        default=(date.today() - timedelta(days=1)).isoformat()
    )

    parser.add_argument(
        "--radius",
        type=int,
        default=25
    )

    parser.add_argument(
        "--batch-days",
        type=int,
        default=7
    )

    parser.add_argument(
        "--batch-stations",
        type=int,
        default=10
    )

    args = parser.parse_args()

    if not TOKEN:
        raise SystemExit(
            "ERROR: SYNOPTIC_TOKEN environment variable not found."
        )

    if args.radius <= 0 or args.batch_days <= 0 or args.batch_stations <= 0:
        raise SystemExit("Radius and batch sizes must be positive.")

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)

    if end < start:
        raise SystemExit("End date must not precede start date.")

    catalog = discover_stations(args.radius)

    if args.catalog_only:
        return

    # Use UTC midnight for requested date boundaries.
    start_time = datetime.combine(
        start, datetime.min.time(), tzinfo=timezone.utc
    )

    end_time = datetime.combine(
        end, datetime.max.time(), tzinfo=timezone.utc
    ).replace(second=0, microsecond=0)

    output, rows, failures = fetch_observations(
        catalog,
        start_time,
        end_time,
        args.batch_days,
        args.batch_stations
    )

    print("\nFINAL STATUS")
    print("=" * 60)

    if failures:
        print("Collection completed with request failures.")
        raise SystemExit(1)

    if rows == 0:
        print("No observations returned. Check station coverage.")
        raise SystemExit(1)

    print("Synoptic collection completed successfully.")
    print(f"Output: {output}")


if __name__ == "__main__":
    main()
