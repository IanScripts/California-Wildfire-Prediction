from pathlib import Path
import pandas as pd

PATH = (
    Path("live")
    / "historical_expanded"
    / "nfdrs_runs"
    / "los_angeles"
    / "historical_indexes.csv"
)

df = pd.read_csv(PATH)

df["DateTime"] = pd.to_datetime(
    df["DateTime"],
    utc=True,
    errors="coerce"
)

indexes = ["BI", "ERC", "SC", "IC", "GSI", "KBDI"]

print("\n===== OVERALL INDEX SUMMARY =====")

for col in indexes:
    values = pd.to_numeric(df[col], errors="coerce")

    print(
        f"{col:5} "
        f"min={values.min():9.3f} "
        f"max={values.max():9.3f} "
        f"mean={values.mean():9.3f} "
        f"positive={(values > 0).sum():6} "
        f"zero={(values == 0).sum():6}"
    )

print("\n===== YEARLY INDEX MAXIMUMS =====")

df["Year"] = df["DateTime"].dt.year

print(
    df.groupby("Year")[indexes]
    .max()
    .to_string()
)

print("\n===== YEARLY INDEX AVERAGES =====")

print(
    df.groupby("Year")[indexes]
    .mean()
    .round(3)
    .to_string()
)

print("\n===== TOP 10 HIGHEST ERC HOURS =====")

print(
    df.nlargest(10, "ERC")[
        ["DateTime", "BI", "ERC", "SC", "IC", "GSI", "KBDI"]
    ].to_string(index=False)
)

print("\n===== TIMESTAMP CHECK =====")

print("Missing timestamps:", df["DateTime"].isna().sum())
print("Duplicate timestamps:", df["DateTime"].duplicated().sum())
print("First timestamp:", df["DateTime"].min())
print("Last timestamp:", df["DateTime"].max())

print("\nHistorical index analysis complete!")
