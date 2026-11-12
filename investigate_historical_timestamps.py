from pathlib import Path
import pandas as pd

ROOT = Path("live/historical_expanded")

weather = pd.read_csv(
    ROOT / "fw21" / "los_angeles_2023_2025.fw21"
)

results = pd.read_csv(
    ROOT / "nfdrs_runs" / "los_angeles"
    / "historical_indexes.csv"
)

weather_time = pd.to_datetime(
    weather["DateTime"],
    utc=True,
    errors="coerce"
)

result_time = pd.to_datetime(
    results["DateTime"],
    format="%Y%m%dT%H%M%SZ",
    utc=True,
    errors="coerce"
)

print("\n===== TIMESTAMP COMPARISON =====")

print("Weather rows:", len(weather))
print("Output rows:", len(results))

print("Weather duplicates:", weather_time.duplicated().sum())
print("Output duplicates:", result_time.duplicated().sum())

print("Invalid weather timestamps:", weather_time.isna().sum())
print("Invalid output timestamps:", result_time.isna().sum())

print("\n===== FIRST 20 ROWS =====")

comparison = pd.DataFrame({
    "Input": weather_time,
    "Output": result_time
})

comparison["DifferenceHours"] = (
    comparison["Output"] - comparison["Input"]
).dt.total_seconds() / 3600

print(comparison.head(20).to_string(index=False))

print("\n===== TIMESTAMP DIFFERENCE FREQUENCIES =====")

print(
    comparison["DifferenceHours"]
    .value_counts(dropna=False)
    .sort_index()
    .to_string()
)

print("\n===== FIRST DUPLICATED OUTPUT TIMESTAMPS =====")

duplicates = results.loc[
    result_time.duplicated(keep=False),
    ["DateTime", "BI", "ERC", "SC", "IC", "GSI", "KBDI"]
]

print(duplicates.head(20).to_string(index=False))

print("\n===== INPUT / OUTPUT WEATHER VALUES =====")

print("First 10 input temperatures:")
print(weather["Temperature(F)"].head(10).tolist())

print("First 10 output temperatures:")

all_outputs = pd.read_csv(
    ROOT / "nfdrs_runs" / "los_angeles"
    / "historical_all.csv"
)

print(all_outputs["Temperature(F)"].head(10).tolist())

print("\nTimestamp investigation complete!")
