"""Prepare and optionally run the currently collected weather sources through NFDRS4.

This is an ADDITIVE demonstration pipeline. It never overwrites the existing
Open-Meteo historical or forecast results. New inputs/configs/results are written
under live/nfdrs_multisource/.

Run on Windows from the NFDRS4 project directory:
  python integrate_weather_sources_nfdrs.py                 # prepare FW21 + configs
  python integrate_weather_sources_nfdrs.py --run            # prepare and run NFDRS4
  python integrate_weather_sources_nfdrs.py --run --sources gfs hrrr

Source handling:
- Open-Meteo GFS: direct model fields.
- NOAA HRRR: direct model fields; accumulated precipitation is differenced by lead hour.
- IEM ASOS: observed temp/RH/precip/wind; solar radiation is an explicitly-labelled
  clear-sky estimate adjusted using METAR sky-cover categories (not a measured value).
- Synoptic: observed temp/RH/wind; precipitation and solar radiation are joined from
  the existing Open-Meteo historical series for the corresponding project city. This
  is a hybrid input, not Synoptic-only weather.

NFDRS4 requires DateTime, Temperature(F), RelativeHumidity(%), Precipitation(in),
WindSpeed(mph), WindAzimuth(degrees), SolarRadiation(W/m2), and SnowFlag.
"""
from __future__ import annotations
import argparse, math, os, re, subprocess, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "live" / "california_sources"
OUT = ROOT / "live" / "nfdrs_multisource"
CLI = ROOT / "build" / "bin" / "NFDRS4_cli.exe"
INIT = ROOT / "data" / "NFDRSInitSample.txt"

CITIES = {
    "Los Angeles": (34.0522, -118.2437),
    "Ventura": (34.2805, -119.2945),
    "Riverside": (33.9806, -117.3755),
    "San Bernardino": (34.1083, -117.2898),
    "Santa Ana": (33.7455, -117.8677),
    "San Diego": (32.7157, -117.1611),
}
# Stable Synoptic airport stations present in the supplied 2026-10-01..07 sample.
SYNOPTIC_STATIONS = {
    "Los Angeles": "KLAX", "Ventura": "KOXR", "Riverside": "KRAL",
    "San Bernardino": "KONT", "Santa Ana": "KSNA", "San Diego": "KSAN",
}
FW_COLS = ["DateTime", "Temperature(F)", "RelativeHumidity(%)", "Precipitation(in)",
           "WindSpeed(mph)", "WindAzimuth(degrees)", "SolarRadiation(W/m2)",
           "SnowFlag", "GustSpeed(mph)", "GustAzimuth(degrees)"]


def read_csv(path, **kwargs):
    if not path.is_file():
        raise FileNotFoundError(path)
    return pd.read_csv(path, low_memory=False, **kwargs)


def clean_numeric(s):
    return pd.to_numeric(s, errors="coerce")


def solar_clear_sky_estimate(ts, lat, lon, cloud_code=None):
    """Simple documented clear-sky proxy, W/m2; not a sensor observation."""
    doy = ts.dayofyear
    hour = ts.hour + ts.minute / 60.0
    decl = math.radians(23.44) * math.sin(2 * math.pi * (284 + doy) / 365.0)
    latr = math.radians(lat)
    solar_hour = hour + (lon / 15.0)
    hour_angle = math.radians(15 * (solar_hour - 12.0))
    cosz = math.sin(latr) * math.sin(decl) + math.cos(latr) * math.cos(decl) * math.cos(hour_angle)
    if cosz <= 0:
        return 0.0
    clear = 1000.0 * cosz * math.exp(-0.14 / max(cosz, 0.05))
    code = str(cloud_code or "").upper()
    factor = 1.0 if code in ("CLR", "SKC", "") else 0.85 if code in ("FEW",) else 0.65 if code in ("SCT",) else 0.35 if code in ("BKN",) else 0.15 if code in ("OVC",) else 0.70
    return max(0.0, min(1400.0, clear * factor))


def make_fw(frame, temp_f, rh, precip_in, wind_mph, wind_dir, solar, gust_mph=None, snow=None):
    out = pd.DataFrame({
        "DateTime": pd.to_datetime(frame["_time"], utc=True, errors="coerce").dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Temperature(F)": clean_numeric(temp_f),
        "RelativeHumidity(%)": clean_numeric(rh),
        "Precipitation(in)": clean_numeric(precip_in),
        "WindSpeed(mph)": clean_numeric(wind_mph),
        "WindAzimuth(degrees)": clean_numeric(wind_dir) % 360,
        "SolarRadiation(W/m2)": clean_numeric(solar),
        "SnowFlag": (clean_numeric(snow).fillna(0).astype(int) if isinstance(snow, pd.Series) else int(snow or 0)),
        "GustSpeed(mph)": clean_numeric(gust_mph) if gust_mph is not None else np.nan,
        "GustAzimuth(degrees)": np.nan,
    })
    # Reject rather than fabricate required values.
    required = FW_COLS[:7]
    out = out.dropna(subset=required).copy()
    out = out[(out["RelativeHumidity(%)"] > 0) & (out["RelativeHumidity(%)"] <= 100)]
    out = out[(out["Temperature(F)"] >= -76) & (out["Temperature(F)"] <= 140)]
    out = out[(out["Precipitation(in)"] >= 0) & (out["Precipitation(in)"] <= 20)]
    out = out[(out["SolarRadiation(W/m2)"] >= 0) & (out["SolarRadiation(W/m2)"] <= 2000)]
    out = out.sort_values("DateTime").drop_duplicates("DateTime", keep="last")
    return out[FW_COLS]


def write_station(source, label, slug, fw, method, station_id):
    if len(fw) < 1:
        return {"source": source, "location": label, "station_id": station_id,
                "rows": len(fw), "status": "SKIPPED no valid rows", "method": method}
    d = OUT / source / slug
    d.mkdir(parents=True, exist_ok=True)
    wx = d / f"{slug}.fw21"
    fw.to_csv(wx, index=False)
    cfg = d / f"{slug}.txt"
    prefix = d / slug
    def posix(p): return p.resolve().as_posix()
    cfg.write_text(
        f'# {source} NFDRS4 integration demo\n# Method: {method}\n'
        f'initFile = "{posix(INIT)}";\nwxFile = "{posix(wx)}";\n'
        f'loadFromStateFile = "";\nsaveToStateFile = "{posix(Path(str(prefix)+"_state.nfdrs"))}";\n'
        f'allOutputsFile = "{posix(Path(str(prefix)+"_all.csv"))}";\n'
        f'indexOutputFile = "{posix(Path(str(prefix)+"_indexes.csv"))}";\n'
        f'fuelMoisturesOutputFile = "{posix(Path(str(prefix)+"_moistures.csv"))}";\n'
        f'outputInterval = "0";\nstationID = "{source}_{slug}";\nuseStoredOutputs = "0";\n',
        encoding="utf-8")
    return {"source": source, "location": label, "station_id": station_id,
            "rows": len(fw), "status": "PREPARED", "method": method,
            "weather_file": str(wx.relative_to(ROOT)), "config_file": str(cfg.relative_to(ROOT))}


def build_gfs(results):
    p = BASE / "open_meteo_gfs" / "open_meteo_gfs_california_forecast.csv"
    d = read_csv(p)
    # Keep current downloaded sample as-is (9 California IEM station locations).
    for (sid, name), g in d.groupby(["station_id", "location_name"], dropna=False):
        g = g.copy(); g["_time"] = pd.to_datetime(g.time, utc=True, errors="coerce")
        fw = make_fw(g, g.temperature_2m, g.relative_humidity_2m, g.precipitation,
                     g.wind_speed_10m, g.wind_direction_10m, g.shortwave_radiation,
                     g.wind_gusts_10m, (g.snowfall > 0).astype(int))
        slug = re.sub(r"[^a-z0-9]+", "_", str(sid).lower()).strip("_")
        results.append(write_station("open_meteo_gfs", str(name), slug, fw,
                                     "Direct GFS forecast fields from Open-Meteo; Fahrenheit/mph/inches as requested.", sid))


def build_hrrr(results):
    p = BASE / "noaa_hrrr" / "noaa_hrrr_california_forecast.csv"
    d = read_csv(p)
    # The six original city-coordinate records are isolated for the core demo.
    d = d[d.location_type.eq("original_socal_location")].copy()
    d["_time"] = pd.to_datetime(d.valid_time_utc, utc=True, errors="coerce")
    d["temperature_f"] = (d.temperature_2m_k - 273.15) * 9/5 + 32
    d["wind_mph"] = d.wind_speed_10m_ms * 2.2369362921
    d["gust_mph"] = d.wind_gust_ms * 2.2369362921
    d["precip_mm"] = pd.to_numeric(d.precipitation_accum_kg_m2, errors="coerce")
    d = d.sort_values(["station_id", "forecast_hour"])
    # GRIB APCP is accumulated from model initialization; convert to hourly increment.
    d["precip_hour_mm"] = d.groupby("station_id").precip_mm.diff()
    d["precip_hour_mm"] = d["precip_hour_mm"].fillna(d["precip_mm"]).clip(lower=0)
    d["precip_in"] = d.precip_hour_mm / 25.4
    for sid, g in d.groupby("station_id"):
        fw = make_fw(g, g.temperature_f, g.relative_humidity_2m_pct, g.precip_in,
                     g.wind_mph, g.wind_direction_10m_deg, g.shortwave_radiation_w_m2,
                     g.gust_mph, 0)
        name = str(g.station_name.iloc[0]); slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        results.append(write_station("noaa_hrrr", name, slug, fw,
                                     "Direct NOAA HRRR forecast fields; Kelvin/m/s/kg m-2 converted to FW21 units; APCP differenced across lead hours.", sid))


def build_iem(results):
    p = BASE / "iem" / "iem_california_observations.csv"
    d = read_csv(p)
    d["_time"] = pd.to_datetime(d.valid, utc=True, errors="coerce")
    for sid, g in d.groupby("station_id"):
        g = g.copy().sort_values("_time")
        cloud = g.get("skyc1", pd.Series(index=g.index, dtype=object))
        solar = [solar_clear_sky_estimate(t, float(g.latitude.iloc[i]), float(g.longitude.iloc[i]), c)
                 for i, (t, c) in enumerate(zip(g._time, cloud))]
        # ASOS wind is knots; precip is inches; temp is Fahrenheit.
        fw = make_fw(g, g.tmpf, g.relh, g.p01i, g.sknt * 1.150779448,
                     g.drct, solar, g.gust * 1.150779448, 0)
        name = str(g.station_name.iloc[0]); slug = re.sub(r"[^a-z0-9]+", "_", str(sid).lower()).strip("_")
        results.append(write_station("iem_asos", name, slug, fw,
                                     "IEM ASOS observed temperature/RH/precip/wind; solar radiation is a clear-sky estimate adjusted by METAR cloud category (not measured).", sid))


def build_synoptic(results):
    p = BASE / "synoptic" / "synoptic_observations_20261001_20261007.csv"
    d = read_csv(p, usecols=["station_id", "station_name", "timestamp_utc", "air_temp_set_1",
                             "relative_humidity_set_1", "wind_speed_set_1", "wind_direction_set_1",
                             "wind_gust_set_1"])
    d["_time"] = pd.to_datetime(d.timestamp_utc, utc=True, errors="coerce").dt.floor("h")
    om = read_csv(ROOT / "live" / "open_meteo_combined_socal.csv")
    om["_time"] = pd.to_datetime(om.time, utc=True, errors="coerce")
    for city, sid in SYNOPTIC_STATIONS.items():
        g = d[d.station_id.eq(sid)].copy()
        if g.empty:
            results.append({"source":"synoptic_hybrid", "location":city,"station_id":sid,"rows":0,"status":"MISSING STATION IN ARCHIVE"})
            continue
        # Hourly average of high-frequency Synoptic station observations.
        g = g.groupby("_time", as_index=False).agg({
            "air_temp_set_1":"mean", "relative_humidity_set_1":"mean",
            "wind_speed_set_1":"mean", "wind_direction_set_1":"mean", "wind_gust_set_1":"max"})
        aux = om[om.location.eq(city)][["_time", "precipitation", "shortwave_radiation", "snowfall"]].copy()
        g = g.merge(aux, on="_time", how="left")
        fw = make_fw(g, g.air_temp_set_1 * 9/5 + 32, g.relative_humidity_set_1,
                     g.precipitation, g.wind_speed_set_1 * 2.2369362921,
                     g.wind_direction_set_1, g.shortwave_radiation,
                     g.wind_gust_set_1 * 2.2369362921,
                     (g.snowfall.fillna(0) > 0).astype(int))
        slug = re.sub(r"[^a-z0-9]+", "_", city.lower()).strip("_")
        results.append(write_station("synoptic_hybrid", city, slug, fw,
                                     f"Synoptic {sid} observed temperature/RH/wind; precipitation and solar radiation joined from Open-Meteo historical data at the same project-city coordinate. Hybrid, not Synoptic-only.", sid))


def run_nfdrs(prepared):
    if not CLI.is_file():
        print(f"NFDRS4 executable not found: {CLI}\nInputs/configs were prepared; run on the Windows machine with build/bin/NFDRS4_cli.exe.")
        return
    if not INIT.is_file():
        raise FileNotFoundError(INIT)
    for item in prepared:
        if item.get("status") != "PREPARED":
            continue
        cfg = ROOT / item["config_file"]
        print(f"\nNFDRS4: {item['source']} / {item['location']} ({item['rows']} weather rows)")
        p = subprocess.run([str(CLI), str(cfg)], cwd=str(ROOT), capture_output=True, text=True, timeout=300)
        if p.stdout.strip(): print(p.stdout.strip())
        if p.stderr.strip(): print(p.stderr.strip())
        item["status"] = "NFDRS4 PASS" if p.returncode == 0 else f"NFDRS4 FAILED ({p.returncode})"
        # Confirm all three primary outputs exist and contain data rows.
        stem = cfg.with_suffix("")
        for suffix in ("_all.csv", "_indexes.csv", "_moistures.csv", "_state.nfdrs"):
            q = Path(str(stem) + suffix)
            if not q.is_file() or q.stat().st_size == 0:
                item["status"] = "OUTPUT CHECK FAILED"
                break


def main():
    ap = argparse.ArgumentParser(description="Prepare/run collected weather sources through NFDRS4.")
    ap.add_argument("--run", action="store_true", help="Run NFDRS4 after generating FW21 and configs.")
    ap.add_argument("--sources", nargs="+", choices=["gfs","hrrr","iem","synoptic"],
                    default=["gfs","hrrr","iem","synoptic"])
    args = ap.parse_args()
    if not INIT.is_file(): raise FileNotFoundError(INIT)
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    builders = {"gfs":build_gfs,"hrrr":build_hrrr,"iem":build_iem,"synoptic":build_synoptic}
    for source in args.sources:
        print(f"\nPreparing {source.upper()}...")
        try: builders[source](results)
        except Exception as exc:
            print(f"SOURCE PREPARATION FAILED ({source}): {exc}")
            results.append({"source":source,"location":"ALL","station_id":"","rows":0,"status":f"PREPARATION FAILED: {exc}"})
    if args.run: run_nfdrs(results)
    report = pd.DataFrame(results)
    report_path = OUT / "integration_manifest.csv"
    report.to_csv(report_path, index=False)
    print("\n" + "="*64 + "\nMULTI-SOURCE NFDRS4 INTEGRATION SUMMARY\n" + "="*64)
    if not report.empty:
        print(report[[c for c in ["source","location","station_id","rows","status"] if c in report]].to_string(index=False))
    print(f"\nManifest: {report_path}")
    print(f"New source-specific files are isolated under: {OUT}")
    if args.run and any("FAILED" in str(x) for x in report.status): raise SystemExit(1)

if __name__ == "__main__": main()
