
"""
Download California ASOS/AWOS observations from the Iowa Environmental Mesonet.

Examples:
  python iem_california.py --catalog-only
  python iem_california.py --start-date 2020-01-01 --end-date 2026-11-05
  python iem_california.py --start-date 2026-10-01 --end-date 2026-11-05 --max-stations 5

The end date is inclusive.
IEM observations are historical/observational data, not forecasts.
CA_ASOS covers participating automated airport stations, not every
weather station in California.
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parent

OUT = ROOT / "live" / "california_sources" / "iem"

CATALOG_FILE = OUT / "iem_california_station_catalog.csv"
OBS_FILE = OUT / "iem_california_observations.csv"
MANIFEST_FILE = OUT / "iem_download_manifest.csv"

IEM_BASE = "https://mesonet.agron.iastate.edu"

NETWORKS = ["CA_ASOS"]

ASOS_CGI = f"{IEM_BASE}/cgi-bin/request/asos.py"

REQUEST_TIMEOUT = 120
PAUSE_SECONDS = 0.35


def get_json(url: str) -> dict:
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": "California-Wildfire-Research/1.0"
        },
    )

    response.raise_for_status()

    return response.json()


def discover_stations() -> pd.DataFrame:

    rows = []

    for network in NETWORKS:

        url = f"{IEM_BASE}/geojson/network/{network}.geojson"

        print(f"Discovering IEM network: {network}")

        payload = get_json(url)

        for feature in payload.get("features", []):

            props = feature.get("properties") or {}

            coords = (
                feature.get("geometry") or {}
            ).get("coordinates") or []

            if len(coords) < 2 or not props.get("sid"):
                continue

            lon, lat = coords[:2]

            rows.append({
                "station_id": str(props["sid"]),
                "network": network,
                "station_name": (
                    props.get("sname")
                    or props.get("name")
                ),
                "latitude": float(lat),
                "longitude": float(lon),
                "elevation_m": props.get("elevation"),
                "state": "CA",
                "source": "IEM station GeoJSON",
                "catalog_retrieved_utc": (
                    datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    )
                ),
            })

    if not rows:
        raise RuntimeError(
            "IEM returned no California station metadata. "
            "Check the network name or IEM service."
        )

    df = pd.DataFrame(rows)

    df = df.drop_duplicates(
        subset=["network", "station_id"]
    )

    df = df.sort_values(
        ["station_name", "station_id"],
        na_position="last",
    )

    OUT.mkdir(parents=True, exist_ok=True)

    df.to_csv(CATALOG_FILE, index=False)

    return df


def parse_iem_csv(text: str) -> pd.DataFrame:
    """
    IEM's format=comma response includes a metadata preamble.

    Locate the actual CSV header instead of assuming that
    the first line is the header.
    """

    lines = text.lstrip("\ufeff").splitlines()

    header_index = None

    for index, line in enumerate(lines):

        try:
            columns = next(csv.reader([line]))

        except csv.Error:
            continue

        columns = [
            column.strip().lower()
            for column in columns
        ]

        if "station" in columns and "valid" in columns:
            header_index = index
            break

    if header_index is None:

        preview = "\n".join(lines[:8])

        raise ValueError(
            "Could not locate the IEM CSV header. "
            f"Response preview:\n{preview}"
        )

    csv_content = "\n".join(lines[header_index:])

    df = pd.read_csv(
        StringIO(csv_content),
        na_values=["M", "T", " "],
        keep_default_na=True,
        low_memory=False,
    )

    df.columns = [
        str(column).strip()
        for column in df.columns
    ]

    return df


def fetch_station_year(
    station: str,
    year: int,
) -> pd.DataFrame:

    # IEM's end date is exclusive.
    # Request January 1 of the following year.

    params = [
        ("station", station),
        ("data", "all"),

        ("year1", str(year)),
        ("month1", "1"),
        ("day1", "1"),

        ("year2", str(year + 1)),
        ("month2", "1"),
        ("day2", "1"),

        ("tz", "Etc/UTC"),
        ("format", "comma"),

        ("latlon", "yes"),
        ("elev", "yes"),

        ("missing", "M"),
        ("trace", "T"),

        ("direct", "no"),

        ("report_type", "3"),
        ("report_type", "4"),
    ]

    response = requests.get(
        ASOS_CGI,
        params=params,
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": "California-Wildfire-Research/1.0"
        },
    )

    response.raise_for_status()

    text = response.text.strip()

    if not text or text.startswith("ERROR"):
        return pd.DataFrame()

    df = parse_iem_csv(text)

    if df.empty:
        return df

    df["station_id"] = station
    df["source"] = "IEM ASOS archive"
    df["data_type"] = "observed"
    df["observation_year"] = year

    return df


def save_manifest(manifest: dict):

    rows = []

    for (station_id, year), bounds in manifest.items():

        rows.append({
            "station_id": station_id,
            "year": year,
            "requested_start": bounds[0].isoformat(),
            "requested_end": bounds[1].isoformat(),
        })

    pd.DataFrame(rows).to_csv(
        MANIFEST_FILE,
        index=False,
    )


def download_observations(
    catalog: pd.DataFrame,
    start: date,
    end: date,
    max_stations: int | None = None,
) -> tuple[int, int]:

    if end < start:
        raise ValueError(
            "--end-date must be on or after --start-date"
        )

    stations = catalog.to_dict("records")

    if max_stations is not None:
        stations = stations[:max_stations]

    manifest = {}

    if MANIFEST_FILE.exists():

        try:

            old_manifest = pd.read_csv(MANIFEST_FILE)

            for row in old_manifest.to_dict("records"):

                manifest[
                    (
                        str(row["station_id"]),
                        int(row["year"]),
                    )
                ] = (
                    date.fromisoformat(
                        str(row["requested_start"])
                    ),
                    date.fromisoformat(
                        str(row["requested_end"])
                    ),
                )

        except Exception as exc:

            print(
                f"Existing manifest could not be read: {exc}"
            )

    total_rows = 0
    failed = 0

    OUT.mkdir(parents=True, exist_ok=True)

    for station_row in stations:

        station = str(station_row["station_id"])

        for year in range(start.year, end.year + 1):

            window_start = max(
                start,
                date(year, 1, 1),
            )

            window_end = min(
                end,
                date(year, 12, 31),
            )

            manifest_key = (station, year)

            previous = manifest.get(manifest_key)

            if (
                previous
                and previous[0] <= window_start
                and previous[1] >= window_end
            ):
                print(
                    f"Already downloaded: {station}/{year}"
                )
                continue

            print(
                f"\nIEM: {station}, {year} "
                f"({window_start} to {window_end})"
            )

            try:

                frame = fetch_station_year(
                    station,
                    year,
                )

                if frame.empty:

                    print(
                        "  No observations returned. "
                        "Leaving eligible for retry."
                    )

                else:

                    if "valid" in frame.columns:

                        timestamps = pd.to_datetime(
                            frame["valid"],
                            utc=True,
                            errors="coerce",
                        )

                        mask = (
                            (timestamps.dt.date >= window_start)
                            & (timestamps.dt.date <= window_end)
                        )

                        frame = frame.loc[mask].copy()

                    for key_name in (
                        "station_name",
                        "latitude",
                        "longitude",
                        "elevation_m",
                    ):

                        frame[key_name] = station_row.get(
                            key_name
                        )

                    if not frame.empty:

                        write_header = not OBS_FILE.exists()

                        frame.to_csv(
                            OBS_FILE,
                            mode="a",
                            header=write_header,
                            index=False,
                        )

                        total_rows += len(frame)

                        print(
                            f"  Retrieved {len(frame)} observations"
                        )

                        # Mark the requested window complete.
                        manifest[manifest_key] = (
                            window_start,
                            window_end,
                        )

                        save_manifest(manifest)

                    else:

                        print(
                            "  No observations in requested date window."
                        )

            except (
                requests.RequestException,
                ValueError,
                pd.errors.ParserError,
                csv.Error,
            ) as exc:

                failed += 1

                print(
                    f"  FAILED {station}/{year}: {exc}"
                )

            time.sleep(PAUSE_SECONDS)

    # Remove duplicate station/time observations.

    if OBS_FILE.exists():

        combined = pd.read_csv(
            OBS_FILE,
            low_memory=False,
        )

        if {"station_id", "valid"}.issubset(
            combined.columns
        ):

            combined = combined.drop_duplicates(
                ["station_id", "valid"],
                keep="last",
            )

        combined.to_csv(
            OBS_FILE,
            index=False,
        )

    return total_rows, failed


def main():

    parser = argparse.ArgumentParser(
        description="Collect California IEM ASOS observations."
    )

    parser.add_argument(
        "--catalog-only",
        action="store_true",
        help="Only refresh the California station catalog.",
    )

    parser.add_argument(
        "--start-date",
        default="2000-01-01",
        help="Inclusive YYYY-MM-DD.",
    )

    parser.add_argument(
        "--end-date",
        default=(
            date.today() - timedelta(days=1)
        ).isoformat(),
        help="Inclusive YYYY-MM-DD.",
    )

    parser.add_argument(
        "--max-stations",
        type=int,
        default=None,
        help="Optional station limit for testing.",
    )

    args = parser.parse_args()

    if args.max_stations is not None and args.max_stations < 1:
        parser.error("--max-stations must be at least 1")

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)

    catalog = discover_stations()

    print(
        f"\nCalifornia IEM station catalog: {len(catalog)} stations"
    )

    print(f"Saved: {CATALOG_FILE}")

    if args.catalog_only:
        return

    rows, failed = download_observations(
        catalog,
        start,
        end,
        args.max_stations,
    )

    print("\n" + "=" * 55)
    print("IEM COLLECTION SUMMARY")
    print("=" * 55)

    print(f"Newly saved rows: {rows}")
    print(f"Failed station-years: {failed}")
    print(f"Observation file: {OBS_FILE}")
    print(f"Manifest file: {MANIFEST_FILE}")

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
