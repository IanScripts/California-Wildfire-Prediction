"""
NFDRS4 additive station configuration + FW21 validation.

Run from the NFDRS4 project directory:
    python prepare_nfdrs_station_configs.py

Writes ONLY under live/nfdrs_station_checks/.
It does not modify original FW21 files, original configs, or NFDRS4 outputs.

Important: generated initialization files have correct known latitudes but
retain SAMPLE fuel, slope, annual precipitation, and initial KBDI parameters.
They are NOT station-calibrated. No NFDRS4 runs are launched automatically.
"""
import csv
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "live" / "nfdrs_multisource"
MANIFEST = BASE / "integration_manifest.csv"
TEMPLATE = ROOT / "data" / "NFDRSInitSample.txt"
OUT = ROOT / "live" / "nfdrs_station_checks"
CITY_COORDS = {
    "Los Angeles": (34.0522, -118.2437),
    "Ventura": (34.2805, -119.2945),
    "Riverside": (33.9806, -117.3755),
    "San Bernardino": (34.1083, -117.2898),
    "Santa Ana": (33.7455, -117.8677),
    "San Diego": (32.7157, -117.1611),
}
REQUIRED = ["DateTime", "Temperature(F)", "RelativeHumidity(%)",
            "Precipitation(in)", "WindSpeed(mph)", "WindAzimuth(degrees)",
            "SolarRadiation(W/m2)", "SnowFlag"]
NUMERIC_BOUNDS = {
    "Temperature(F)": (-76, 140),
    "RelativeHumidity(%)": (0, 100),
    "Precipitation(in)": (0, 20),
    "WindSpeed(mph)": (0, 200),
    "WindAzimuth(degrees)": (0, 360),
    "SolarRadiation(W/m2)": (0, 2000),
    "SnowFlag": (0, 1),
}
def posix(path):
    return path.resolve().as_posix()

def lookup_coordinates():
    by_id = {}
    catalog = ROOT / "live/california_sources/iem/iem_california_station_catalog.csv"
    if catalog.exists():
        for r in pd.read_csv(catalog, low_memory=False).to_dict("records"):
            try:
                sid = str(r["station_id"]).strip()
                lat, lon = float(r["latitude"]), float(r["longitude"])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    by_id[sid] = (lat, lon)
            except (ValueError, TypeError, KeyError):
                continue
    return by_id

def check_weather(path):
    result = {"rows": 0, "errors": [], "warnings": [], "start": "", "end": ""}
    if not path.is_file():
        result["errors"].append("Missing FW21 file")
        return result
    try:
        d = pd.read_csv(path, low_memory=False)
    except Exception as exc:
        result["errors"].append(f"CSV read failed: {exc}")
        return result
    result["rows"] = len(d)
    missing_cols = [c for c in REQUIRED if c not in d.columns]
    if missing_cols:
        result["errors"].append("Missing required columns: " + ", ".join(missing_cols))
        return result
    if d.empty:
        result["errors"].append("No weather records")
        return result
    timestamps = pd.to_datetime(d["DateTime"], utc=True, errors="coerce")
    bad_times = int(timestamps.isna().sum())
    if bad_times:
        result["errors"].append(f"{bad_times} invalid timestamps")
    dup = int(timestamps[timestamps.notna()].duplicated().sum())
    if dup:
        result["errors"].append(f"{dup} duplicate timestamps")
    ordered = timestamps.dropna()
    if len(ordered):
        result["start"] = ordered.min().isoformat()
        result["end"] = ordered.max().isoformat()
        if not ordered.is_monotonic_increasing:
            result["errors"].append("Timestamps out of order")
        diffs = ordered.diff().dropna()
        if (diffs <= pd.Timedelta(0)).any():
            result["errors"].append("Non-increasing time sequence")
        gaps = int((diffs > pd.Timedelta(hours=1)).sum())
        if gaps:
            result["warnings"].append(f"{gaps} intervals greater than one hour")
        subhour = int((diffs < pd.Timedelta(hours=1)).sum())
        if subhour:
            result["warnings"].append(f"{subhour} subhourly intervals (check intended cadence)")
        if len(ordered) < 24:
            result["warnings"].append("Less than 24 hours of records: inadequate spin-up")
        elif len(ordered) < 24 * 30:
            result["warnings"].append("Less than 30 days of records: fuel-moisture spin-up may be inadequate")
    for field, (low, high) in NUMERIC_BOUNDS.items():
        numeric = pd.to_numeric(d[field], errors="coerce")
        missing = int(numeric.isna().sum())
        invalid = int(((numeric < low) | (numeric > high)).sum())
        if missing:
            result["errors"].append(f"{field}: {missing} missing/non-numeric values")
        if invalid:
            result["errors"].append(f"{field}: {invalid} outside plausible range [{low},{high}]")
        if field == "SnowFlag":
            invalid_integer = int((numeric.notna() & (~numeric.isin([0, 1]))).sum())
            if invalid_integer and not invalid:
                result["errors"].append(f"SnowFlag: {invalid_integer} not 0 or 1")
    for optional in ("GustSpeed(mph)", "GustAzimuth(degrees)"):
        if optional in d:
            numeric = pd.to_numeric(d[optional], errors="coerce")
            if optional.startswith("GustSpeed"):
                bad = int(((numeric < 0) | (numeric > 250)).sum())
            else:
                bad = int(((numeric < 0) | (numeric > 360)).sum())
            if bad:
                result["warnings"].append(f"{optional}: {bad} implausible optional values")
    if "WindSpeed(mph)" in d:
        ws = pd.to_numeric(d["WindSpeed(mph)"], errors="coerce")
        if int((ws == 0).sum()) > 0:
            result["warnings"].append("Calm wind observations present; verify wind-direction convention")
    return result

def replace_config_value(text, name, value):
    # Only replace a real, uncommented top-level key.
    pattern = re.compile(rf'(?m)^(\s*{re.escape(name)}\s*=\s*)"[^"]*"\s*;?\s*$')
    found = list(pattern.finditer(text))
    if len(found) != 1:
        raise ValueError(f"Expected one top-level {name} in sample init; found {len(found)}")
    return pattern.sub(lambda m: f'{m.group(1)}"{value}";', text, count=1)

def main():
    if not MANIFEST.exists() or not TEMPLATE.exists():
        raise SystemExit("Missing integration_manifest.csv or NFDRSInitSample.txt")
    OUT.mkdir(parents=True, exist_ok=True)
    station_coords = lookup_coordinates()
    manifest = pd.read_csv(MANIFEST, dtype=str).fillna("")
    template = TEMPLATE.read_text(encoding="utf-8-sig")
    report = []
    for i, r in enumerate(manifest.to_dict("records"), 1):
        source, location, sid = r.get("source", ""), r.get("location", ""), r.get("station_id", "")
        fwrel = r.get("weather_file", "")
        cfgrel = r.get("config_file", "")
        errors, warnings = [], []
        weather = ROOT / fwrel if fwrel else None
        check = check_weather(weather) if weather else {"rows":0,"errors":["No weather_file in manifest"],"warnings":[],"start":"","end":""}
        errors.extend(check["errors"])
        warnings.extend(check["warnings"])
        coords = CITY_COORDS.get(location) if sid.startswith("CITY_") or source == "synoptic_hybrid" else station_coords.get(sid)
        # HRRR city and Synoptic city use project reference coordinates;
        # station-based GFS/IEM use catalog station coordinates.
        if coords is None and location in CITY_COORDS and source in ("noaa_hrrr", "synoptic_hybrid"):
            coords = CITY_COORDS[location]
        if coords is None:
            errors.append("Coordinates unavailable; no initialization generated")
        if source == "iem_asos":
            warnings.append("IEM solar radiation is estimated, not observed")
        if source == "synoptic_hybrid":
            warnings.append("Synoptic uses Open-Meteo precipitation and solar; hybrid source")
        if source == "noaa_hrrr":
            warnings.append("Short forecast run; NFDRS4 state not warmed up")
        warnings.append("Fuel model, slope, annual precipitation, start KBDI and observation hour are UNCALIBRATED sample settings")
        # UTC source timestamps and Pacific standard offset; DST requires explicit
        # station-specific handling and verification before operational use.
        warnings.append("timeZoneOffset=-8 is fixed Pacific Standard Time; verify daylight-saving handling")
        new_init = new_cfg = ""
        if coords is not None and cfgrel:
            try:
                dest = OUT / f"{i:02d}_{source}_{re.sub(r'[^a-z0-9]+','_',sid.lower())}"
                dest.mkdir(parents=True, exist_ok=True)
                init = replace_config_value(template, "latitude", f"{coords[0]:.6f}")
                init = replace_config_value(init, "timeZoneOffset", "-8")
                init_path = dest / "station_init.txt"
                init_path.write_text(
                    "# RESEARCH PREVIEW: latitude from project/catalog; other fuel/climate settings remain SAMPLE.\n"
                    "# Fixed PST -8; daylight-saving behavior requires verification.\n" + init, encoding="utf-8")
                new_init = str(init_path.relative_to(ROOT))
                original_cfg = ROOT / cfgrel
                if not original_cfg.is_file():
                    errors.append("Original run config missing")
                else:
                    cfg_text = original_cfg.read_text(encoding="utf-8-sig")
                    cfg_text = replace_config_value(cfg_text, "initFile", posix(init_path))
                    # Keep new outputs isolated. Never append to original files.
                    for key, suffix in [
                        ("allOutputsFile", "_all.csv"),
                        ("indexOutputFile", "_indexes.csv"),
                        ("fuelMoisturesOutputFile", "_moistures.csv"),
                        ("saveToStateFile", "_state.nfdrs"),
                    ]:
                        cfg_text = replace_config_value(cfg_text, key, posix(dest / ("checked" + suffix)))
                    cfg_path = dest / "checked_run.txt"
                    cfg_path.write_text(cfg_text, encoding="utf-8")
                    new_cfg = str(cfg_path.relative_to(ROOT))
            except Exception as exc:
                errors.append(f"Could not create station config: {exc}")
        report.append({
            "source": source, "location": location, "station_id": sid,
            "latitude": coords[0] if coords else "", "longitude": coords[1] if coords else "",
            "weather_records": check["rows"], "first_utc": check["start"], "last_utc": check["end"],
            "validation": "FAIL" if errors else ("REVIEW" if warnings else "PASS"),
            "errors": " | ".join(errors), "warnings": " | ".join(warnings),
            "station_init_file": new_init, "checked_run_config": new_cfg,
        })
        print(f"{source:19} {location[:26]:26} {report[-1]['validation']}")
    dest = OUT / "station_validation_report.csv"
    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=report[0].keys() if report else [])
        writer.writeheader()
        writer.writerows(report)
    counts = Counter(x["validation"] for x in report)
    print(f"\nChecked {len(report)} inputs. Status counts: {dict(counts)}")
    print(f"Report: {dest}")
    print("Original weather, configs, results, and dashboard were not changed.")
    print("Do NOT treat generated initialization files as calibrated site configurations.")
if __name__ == "__main__":
    main()
