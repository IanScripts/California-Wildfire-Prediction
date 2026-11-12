from pathlib import Path
import pandas as pd

from integrate_weather_sources_nfdrs import make_fw

ROOT = Path(__file__).resolve().parent

INPUT = (
    ROOT / "live" / "historical_expanded"
    / "open_meteo_socal_2023_2025.csv"
)

OUTPUT = (
    ROOT / "live" / "historical_expanded" / "fw21"
)

def main():
    df = pd.read_csv(INPUT)

    expected_rows = 26304

    prepared = []

    for city, group in df.groupby("location"):
        group = group.copy()

        group["_time"] = pd.to_datetime(
            group["time"],
            utc=True,
            errors="coerce"
        )

        snow_flag = (
            group["snowfall"].fillna(0) > 0
        ).astype(int)

        fw = make_fw(
            group,
            group["temperature_2m"],
            group["relative_humidity_2m"],
            group["precipitation"],
            group["wind_speed_10m"],
            group["wind_direction_10m"],
            group["shortwave_radiation"],
            None,
            snow_flag
        )

        print(
            f"{city}: input={len(group)}, "
            f"FW21={len(fw)}"
        )

        if len(fw) != expected_rows:
            raise ValueError(
                f"{city}: expected {expected_rows} FW21 rows, "
                f"got {len(fw)}"
            )

        slug = city.lower().replace(" ", "_")
        path = OUTPUT / f"{slug}_2023_2025.fw21"

        if path.exists():
            raise FileExistsError(
                f"Refusing to overwrite existing file: {path}"
            )

        prepared.append((path, fw))

    OUTPUT.mkdir(parents=True, exist_ok=True)

    for path, fw in prepared:
        fw.to_csv(path, index=False)
        print(f"Saved: {path}")

    print("\nHistorical FW21 conversion complete!")

if __name__ == "__main__":
    main()
