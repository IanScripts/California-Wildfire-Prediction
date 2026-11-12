
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIVE = ROOT / "live"

FILES = [
    LIVE / "open_meteo_historical_socal.csv",
    LIVE / "open_meteo_gap_socal.csv",
    LIVE / "open_meteo_socal.csv",
]

OUTPUT = LIVE / "open_meteo_combined_socal.csv"


def main():
    frames = []

    for path in FILES:
        if not path.exists():
            raise FileNotFoundError(
                f"Missing input file: {path}"
            )

        df = pd.read_csv(path)
        df["time"] = pd.to_datetime(
            df["time"], utc=True
        )

        frames.append(df)

        print(f"Loaded {path.name}: {len(df)} rows")

    combined = pd.concat(
        frames, ignore_index=True
    )

    # Keep one record per location and UTC hour.
    # Later files take priority if timestamps overlap.
    combined = combined.drop_duplicates(
        subset=["location", "time"],
        keep="last"
    )

    combined = combined.sort_values(
        ["location", "time"]
    ).reset_index(drop=True)

    # Verify hourly continuity for each location.
    print("\nHourly continuity check:")

    for location, group in combined.groupby("location"):
        expected = pd.date_range(
            start=group["time"].min(),
            end=group["time"].max(),
            freq="h",
            tz="UTC"
        )

        missing = expected.difference(
            pd.DatetimeIndex(group["time"])
        )

        print(
            f"{location}: "
            f"{len(group)} records, "
            f"{len(missing)} missing hours"
        )

        if len(missing):
            print(
                "  First missing timestamps:",
                list(missing[:5])
            )

    # Store UTC timestamps in the same format
    # expected by the existing FW21 converter.
    combined["time"] = combined[
        "time"
    ].dt.strftime("%Y-%m-%dT%H:%M:%S")

    combined.to_csv(
        OUTPUT, index=False
    )

    print(f"\nSaved: {OUTPUT}")
    print(f"Total records: {len(combined)}")
    print(
        "Data types:",
        combined["data_type"].value_counts().to_dict()
    )


if __name__ == "__main__":
    main()
