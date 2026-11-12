
"""FEMS -> NFDRS4 weather processing pipeline for Windows."""

import argparse
import csv
import io
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ----------------------------------
# NFDRS4 PROJECT CONFIGURATION
# ----------------------------------

ROOT = Path(__file__).resolve().parent

STATION_ID = "241513"

SEED_FILE = ROOT / "data" / "241513_2001_2017.fw21"
INIT_FILE = ROOT / "data" / "NFDRSInitSample.txt"
CLI = ROOT / "build" / "bin" / "NFDRS4_cli.exe"

LIVE = ROOT / "live"
ARCHIVE = LIVE / "weather_archive.fw21"

DOWNLOAD_URL = (
    "https://fems.fs2c.usda.gov/api/climatology/download-weather"
)

FIELDS = [
    "StationId",
    "DateTime",
    "Temperature(F)",
    "RelativeHumidity(%)",
    "Precipitation(in)",
    "WindSpeed(mph)",
    "WindAzimuth(degrees)",
    "SolarRadiation(W/m2)",
    "SnowFlag",
]

NUMERIC_FIELDS = FIELDS[2:-1]


# ----------------------------------
# DATE/TIME VALIDATION
# ----------------------------------

def parse_time(value):
    dt = datetime.fromisoformat(
        value.strip().replace("Z", "+00:00")
    )

    if dt.utcoffset() is None:
        raise ValueError(
            f"FW21 DateTime needs a UTC offset: {value}"
        )

    return dt


# ----------------------------------
# READ AND VALIDATE WEATHER DATA
# ----------------------------------

def read_fw21(content, description):
    """Read FW21 data and skip incomplete weather observations."""

    reader = csv.DictReader(
        io.StringIO(content.lstrip("\ufeff")),
        skipinitialspace=True,
    )

    if not reader.fieldnames:
        raise ValueError(
            f"{description}: missing CSV header"
        )

    header = {
        s.casefold(): s
        for s in reader.fieldnames
        if s
    }

    for name in FIELDS[1:]:
        if name.casefold() not in header:
            raise ValueError(
                f"{description}: missing FW21 column {name}; "
                f"received {reader.fieldnames}"
            )

    actual_ids = set()
    records = {}

    skipped = 0
    skipped_examples = []

    for line_num, raw in enumerate(reader, start=2):

        if not raw or not any(
            (v or "").strip()
            for v in raw.values()
            if isinstance(v, str)
        ):
            continue

        if None in raw:
            raise ValueError(
                f"{description} line {line_num}: too many CSV fields"
            )

        source_id = (
            raw.get(header.get("stationid", ""), "") or ""
        ).strip()

        if source_id:
            actual_ids.add(source_id)

        row = {
            name: (
                raw.get(header.get(name.casefold(), ""), "") or ""
            ).strip()
            for name in FIELDS[1:]
        }

        # Skip incomplete observations
        missing_fields = [
            name for name in NUMERIC_FIELDS
            if not row[name]
        ]

        if missing_fields:
            skipped += 1

            if len(skipped_examples) < 10:
                skipped_examples.append(
                    f"line {line_num}: missing "
                    f"{', '.join(missing_fields)}"
                )

            continue

        # Validate numeric measurements
        for name in NUMERIC_FIELDS:
            float(row[name])

        # Validate snow flag
        snow = row["SnowFlag"].lower()

        if snow in ("", "false", "0"):
            row["SnowFlag"] = "0"

        elif snow in ("true", "1"):
            row["SnowFlag"] = "1"

        else:
            raise ValueError(
                f"{description} line {line_num}: "
                f"invalid SnowFlag {snow!r}"
            )

        dt = parse_time(row["DateTime"])

        row["StationId"] = STATION_ID
        row["DateTime"] = dt.isoformat(timespec="seconds")

        key = dt.astimezone(timezone.utc)

        records[key] = row

    # Report skipped observations
    if skipped:
        print(
            f"Warning: skipped {skipped} incomplete "
            f"weather observations in {description}."
        )

        for example in skipped_examples:
            print(f"  {example}")

        if skipped > len(skipped_examples):
            print(
                f"  ... and "
                f"{skipped - len(skipped_examples)} more"
            )

    if len(actual_ids) > 1:
        raise ValueError(
            f"{description}: several station IDs "
            f"in this file: {actual_ids}"
        )

    if not records:
        raise ValueError(
            f"{description}: no valid observations"
        )

    if actual_ids and STATION_ID not in actual_ids:
        print(
            f"Warning: file station ID "
            f"{next(iter(actual_ids))} differs "
            f"from configured {STATION_ID}."
        )
        print(
            "Confirm that the seed and FEMS query "
            "refer to the SAME physical station."
        )

    return records


# ----------------------------------
# FETCH RECENT FEMS WEATHER DATA
# ----------------------------------

def fetch_recent():

    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=72)

    params = {
        "stationIds": STATION_ID,
        "startDate": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDate": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset": "observation",
        "dataFormat": "fw21",
        "dataIncrement": "hourly",
        "stationtypes": "RAWS(SATNFDRS)",
    }

    print(
        f"Requesting FEMS observations for "
        f"{STATION_ID} "
        f"({start:%Y-%m-%d} to {now:%Y-%m-%d} UTC)"
    )

    response = requests.get(
        DOWNLOAD_URL,
        params=params,
        timeout=60,
        headers={
            "User-Agent": "NFDRS4-capstone-prototype/1.0"
        },
    )

    if response.status_code in (401, 403):
        raise RuntimeError(
            "FEMS refused automated access (401/403). "
            "Use --local-file or arrange official API access."
        )

    response.raise_for_status()

    if response.content.startswith(b"PK\x03\x04"):
        raise ValueError(
            "FEMS returned ZIP data; "
            "use a one-station FW21 download instead"
        )

    return response.content.decode("utf-8-sig")


# ----------------------------------
# WRITE WEATHER ARCHIVE
# ----------------------------------

def write_fw21(path, records):

    with path.open(
        "w",
        encoding="utf-8",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=FIELDS,
            lineterminator="\n"
        )

        writer.writeheader()

        writer.writerows(
            records[t]
            for t in sorted(records)
        )


# ----------------------------------
# GENERATE NFDRS4 CONFIGURATION
# ----------------------------------

def write_config(path, weather, out_dir):

    def q(p):
        return str(p.resolve()).replace("\\", "/")

    path.write_text(
        f'initFile = "{q(INIT_FILE)}";\n'
        f'wxFile = "{q(weather)}";\n'
        'loadFromStateFile = "";\n'
        'saveToStateFile = "";\n'
        f'allOutputsFile = "{q(out_dir / "NFDRSoutput.csv")}";\n'
        f'indexOutputFile = "{q(out_dir / "NFDRSIndexes.csv")}";\n'
        f'fuelMoisturesOutputFile = "{q(out_dir / "NFDRSMoistures.csv")}";\n'
        'outputInterval = "0";\n'
        'useStoredOutputs = "0";\n'
        f'stationID = "{STATION_ID}";\n',
        encoding="utf-8"
    )


# ----------------------------------
# MAIN PIPELINE
# ----------------------------------

def main():

    parser = argparse.ArgumentParser(
        description="Update local FW21 archive and rerun NFDRS4"
    )

    group = parser.add_mutually_exclusive_group(
        required=True
    )

    group.add_argument(
        "--local-file",
        type=Path,
        help="Use a manually downloaded FW21 file"
    )

    group.add_argument(
        "--fetch",
        action="store_true",
        help="Try the FEMS FW21 HTTP endpoint"
    )

    args = parser.parse_args()

    if STATION_ID.startswith("REPLACE_"):
        parser.error(
            "Edit STATION_ID at the top of this script first"
        )

    if not CLI.is_file() or not INIT_FILE.is_file():
        parser.error(
            f"Missing executable or initialization file: "
            f"{CLI} / {INIT_FILE}"
        )

    if not ARCHIVE.is_file() and not SEED_FILE.is_file():
        parser.error(
            f"Need an initial history file: {SEED_FILE}"
        )

    LIVE.mkdir(exist_ok=True)

    history_path = (
        ARCHIVE if ARCHIVE.is_file() else SEED_FILE
    )

    history = read_fw21(
        history_path.read_text(encoding="utf-8-sig"),
        str(history_path)
    )

    if args.fetch:
        incoming = read_fw21(
            fetch_recent(),
            "FEMS download"
        )

    else:
        path = args.local_file.resolve()

        incoming = read_fw21(
            path.read_text(encoding="utf-8-sig"),
            str(path)
        )

    new_count = len(
        set(incoming) - set(history)
    )

    changed_count = sum(
        1 for t, row in incoming.items()
        if t in history and history[t] != row
    )

    history.update(incoming)

    if (
        new_count == 0
        and changed_count == 0
        and ARCHIVE.is_file()
        and all(
            (LIVE / name).is_file()
            for name in (
                "NFDRSoutput.csv",
                "NFDRSIndexes.csv",
                "NFDRSMoistures.csv"
            )
        )
    ):
        print(
            "No new or corrected observations. "
            "Outputs already up to date."
        )
        return

    print(
        f"Total observations: {len(history)} | "
        f"New: {new_count} | "
        f"Corrected: {changed_count}"
    )

    ordered_times = sorted(history)

    long_gaps = [
        b - a
        for a, b in zip(
            ordered_times,
            ordered_times[1:]
        )
        if b - a > timedelta(hours=2)
    ]

    if long_gaps:
        print(
            f"Warning: {len(long_gaps)} gaps "
            f"longer than two hours in weather history."
        )

    # Stage results before publishing
    with tempfile.TemporaryDirectory(
        prefix="stage_",
        dir=LIVE
    ) as temp_dir:

        stage = Path(temp_dir)

        wx = stage / "weather_archive.fw21"
        config = stage / "RunNFDRS_live.txt"

        write_fw21(wx, history)
        write_config(config, wx, stage)

        completed = subprocess.run(
            [str(CLI), str(config)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180
        )

        print(completed.stdout.strip())

        if completed.stderr:
            print(completed.stderr.strip())

        if completed.returncode:
            raise RuntimeError(
                f"NFDRS4 exited with code "
                f"{completed.returncode}"
            )

        outputs = [
            "NFDRSoutput.csv",
            "NFDRSIndexes.csv",
            "NFDRSMoistures.csv"
        ]

        for name in outputs:

            output = stage / name

            if not output.is_file():
                raise RuntimeError(
                    f"NFDRS4 failed to create {name}"
                )

            with output.open(
                newline="",
                encoding="utf-8-sig"
            ) as f:

                actual_rows = sum(
                    1 for _ in csv.DictReader(f)
                )

            if actual_rows != len(history):
                raise RuntimeError(
                    f"{name} has {actual_rows} rows; "
                    f"expected {len(history)}. "
                    "Old outputs preserved."
                )

        for name in outputs:
            os.replace(
                stage / name,
                LIVE / name
            )

        os.replace(wx, ARCHIVE)

    latest = max(history)

    print(
        f"Success! Latest observation (UTC): "
        f"{latest.isoformat()}"
    )

    age = datetime.now(timezone.utc) - latest

    if age > timedelta(hours=3):
        print(
            f"Warning: weather is {age} old. "
            "Do not label the indices 'live'."
        )

    print(
        f"Results: {LIVE / 'NFDRSIndexes.csv'}"
    )


if __name__ == "__main__":
    try:
        main()

    except (
        OSError,
        ValueError,
        requests.RequestException,
        RuntimeError,
        subprocess.TimeoutExpired
    ) as error:

        raise SystemExit(
            f"Pipeline stopped without publishing "
            f"new outputs: {error}"
        )
