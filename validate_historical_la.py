from pathlib import Path
import pandas as pd

ROOT = Path("live/historical_expanded")
WX = ROOT / "fw21" / "los_angeles_2023_2025.fw21"
OUT = ROOT / "nfdrs_runs" / "los_angeles"

files = {
    "Weather": WX,
    "All outputs": OUT / "historical_all.csv",
    "Indexes": OUT / "historical_indexes.csv",
    "Moistures": OUT / "historical_moistures.csv",
}

for label, path in files.items():
    print(f"\n===== {label} =====")

    df = pd.read_csv(path)

    print("Rows:", len(df))
    print("Columns:", list(df.columns))

    print("\nFirst 2 rows:")
    print(df.head(2).to_string(index=False))

    print("\nLast 2 rows:")
    print(df.tail(2).to_string(index=False))

    print("\nMissing values:")
    print(df.isna().sum().to_string())

print("\n===== WEATHER FILE END CHECK =====")

with WX.open("r", encoding="utf-8-sig") as f:
    lines = f.readlines()

print("Physical lines:", len(lines))
print("Last 5 lines:")

for number in range(max(0, len(lines) - 5), len(lines)):
    print(f"{number + 1}: {lines[number].rstrip()!r}")

print("\nValidation inspection complete!")
