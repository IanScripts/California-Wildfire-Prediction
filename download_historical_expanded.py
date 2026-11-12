from pathlib import Path
import pandas as pd
from open_meteo_api import LOCATIONS, get_historical_weather

START_DATE = "2023-01-01"
END_DATE = "2025-12-31"

OUTPUT = (
    Path(__file__).resolve().parent
    / "live"
    / "historical_expanded"
    / "open_meteo_socal_2023_2025.csv"
)

def main():
    if OUTPUT.exists():
        print(f"Output already exists: {OUTPUT}")
        print("No files were changed.")
        return

    frames = []

    for name, (lat, lon) in LOCATIONS.items():
        print(f"Downloading: {name}", flush=True)

        try:
            df = get_historical_weather(
                name, lat, lon, START_DATE, END_DATE
            )
            print(f"  Retrieved {len(df)} rows", flush=True)
            frames.append(df)
        except Exception as error:
            print(f"  FAILED: {error}", flush=True)
            print("Download incomplete; no CSV saved.")
            return

    combined = pd.concat(frames, ignore_index=True)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(OUTPUT, index=False)

    print("\nDownload complete!")
    print(f"Total records: {len(combined)}")
    print(f"Saved to: {OUTPUT}")

if __name__ == "__main__":
    main()
