# California-wide weather data-source integration

This addition expands the existing six-location NFDRS4 research project with
California-wide station observations and two forecast-model sources. It does
**not** replace the current Open-Meteo historical workflow, rewrite NFDRS4
configuration, or change `run_pipeline.py`.

## Sources and what they contribute

### 1. Iowa Environmental Mesonet (IEM) — station observations

`iem_california.py` discovers the IEM `CA_ASOS` network through its station
GeoJSON service and saves a station catalog with station IDs, names, latitude,
longitude, and elevation where available. It downloads parsed ASOS/AWOS/METAR
archive fields including:

- `tmpf`: air temperature (°F)
- `dwpf`: dew point (°F)
- `relh`: relative humidity (%), when reported/available
- `drct`: wind direction (degrees)
- `sknt`: wind speed (knots)
- `gust`: gust speed (knots)
- `p01i`: one-hour precipitation (inches), subject to reporting conventions
- `alti`, `mslp`, `vsby`: pressure and visibility fields

IEM's archive is observational, but the archive is not a complete inventory of
every weather station in California. The `CA_ASOS` network is primarily
automated airport stations. IEM says the archive is minimally quality
controlled and some sites have data back to 2000. Missing fields are left
missing, not imputed. Do not treat an absent report as zero precipitation.

The script requests station-years individually and can resume station-year
pairs already present in its output. The default historical start is
2000-01-01; each station's actual first observation will vary.

### 2. Open-Meteo GFS — forecast model

`open_meteo_gfs.py` calls the GFS-specific Open-Meteo endpoint and requests up
to 16 days of hourly forecast data for every discovered IEM station coordinate,
plus the six original project city coordinates. Requested fields include
temperature, relative humidity, dew point, precipitation/rain/showers/snowfall,
wind speed/direction/gusts, shortwave radiation, pressure, cloud cover, vapor
pressure deficit, shallow soil temperature/moisture, and CAPE.

The output records the requested station coordinate and the model grid-cell
coordinate returned by the API. This distinction matters because the model
grid cell is not necessarily exactly at the station. These are modeled
forecasts, not physical station observations.

The GFS API is a forecast source, not a multi-decade historical archive. The
project's existing Open-Meteo historical/reanalysis data remains a separate
historical source. Historical archived forecast-run verification can be added
later using Open-Meteo's Historical Forecast / Previous Runs APIs.

### 3. NOAA HRRR — high-resolution regional model

`noaa_hrrr_california.py` uses Herbie to access NOAA's public HRRR GRIB2
archive. The HRRR model has approximately 3-km grid spacing and hourly model
cycles over the contiguous United States. The script extracts the nearest
model grid cell to each IEM station and the six original project city points.

Fields attempted include 2-m temperature, 2-m dew point, 2-m relative humidity,
10-m U/V wind components, gust, accumulated precipitation, shortwave
radiation, and surface pressure. Wind speed and direction are derived from
the U/V components when both are present. Field availability varies by GRIB
product and run; unavailable fields are recorded as missing.

HRRR is a numerical weather prediction model, not a station observation
network and not a REST/JSON API. The official Rapid Refresh page describes the
model; the raw GRIB2 archive is accessed through NOAA's public data
distribution. Historical cycles can be requested by passing `--cycle`.
The script saves extracted point data and Herbie's downloaded GRIB files in
`live/california_sources/noaa_hrrr/grib_cache/`.

## Install

From the NFDRS4 project root:

```powershell
python -m pip install -r requirements_data_sources.txt
```

If only IEM and Open-Meteo GFS are needed, the core packages `requests` and
`pandas` are sufficient. HRRR extraction additionally requires Herbie,
xarray, cfgrib, and ecCodes. Installation of ecCodes can vary by Windows
environment; if HRRR dependencies fail, the IEM and GFS collectors can still
run independently.

## Run commands

Refresh the California IEM station catalog only:

```powershell
python iem_california.py --catalog-only
```

Collect the longest IEM ASOS archive window requested by this script
(2000 through yesterday; per-station availability varies):

```powershell
python iem_california.py --start-date 2000-01-01
```

Test a small sample before a statewide download:

```powershell
python iem_california.py --start-date 2026-10-01 --max-stations 5
```

Collect GFS forecasts for all catalogued station coordinates and the six
project city points:

```powershell
python open_meteo_gfs.py --forecast-days 16
```

Collect NOAA HRRR data for the latest completed cycle:

```powershell
python noaa_hrrr_california.py --forecast-hours 0 1 2 3 4 5
```

Collect a specific historical HRRR model cycle:

```powershell
python noaa_hrrr_california.py --cycle 2024-08-15T12 --forecast-hours 0 1 2 3 4 5
```

Run the staged collector:

```powershell
python run_california_sources.py --start-date 2000-01-01
```

Include HRRR:

```powershell
python run_california_sources.py --start-date 2000-01-01 --with-hrrr
```

Validate outputs:

```powershell
python validate_california_sources.py
```

For a first run, use `--max-stations 3` with the individual collectors. A
full statewide, multi-decade IEM pull can require many requests and substantial
time. It should be run in batches and monitored rather than assumed to finish
quickly.

## Output layout

```text
live/
  california_sources/
    iem/
      iem_california_station_catalog.csv
      iem_california_observations.csv
    open_meteo_gfs/
      open_meteo_gfs_california_forecast.csv
      gfs_response_metadata.csv
      gfs_failures.csv             (only if requests fail)
    noaa_hrrr/
      noaa_hrrr_california_forecast.csv
      hrrr_failures.csv            (only if fields/runs fail)
      grib_cache/
```

## How this supports the research questions

- IEM gives observed weather reports at participating airport stations.
- Open-Meteo GFS provides forecast conditions at each station/city coordinate.
- HRRR provides a finer-grid model perspective around the same points.
- The original six city coordinates remain available for continuity with the
  existing project dashboard.
- Side-by-side comparisons can quantify model-to-observation error and help
  identify which weather variables are most informative for NFDRS4 inputs and
  later ignition/spread models.

## Important scientific limitations

1. "All California stations" here means all stations returned by the IEM
   `CA_ASOS` network discovery endpoint, plus the six city points for model
   extraction. It does not mean every CIMIS, RAWS, cooperative, mesonet,
   agricultural, or private station in California. More networks must be
   catalogued separately to broaden coverage.
2. IEM observations may be irregular, delayed, missing, or lightly quality
   controlled. Run quality checks before analysis.
3. GFS and HRRR values are model grid-cell estimates. They must not be labeled
   as station measurements.
4. Precipitation definitions, time aggregation, units, model initialization,
   and valid times differ across sources. Harmonize these before direct
   comparisons.
5. NFDRS4 FW21 currently expects a particular set of hourly weather fields.
   Do not feed every new variable directly into NFDRS4. Keep rich source data
   in the canonical CSVs; create a separate validated FW21 conversion step
   using only compatible fields.
6. A successful HTTP response or a generated CSV proves retrieval, not
   meteorological accuracy or wildfire-prediction skill.
7. Fire occurrence labels and vegetation/fuel/topographic information are
   still needed to train and evaluate a model that predicts ignition risk.
   Weather-based NFDRS indices are fire-danger indicators, not a direct
   probability that a fire will start.

## Official references

- Open-Meteo GFS & RRFS API: https://open-meteo.com/en/docs/gfs-api
- Open-Meteo Historical Forecast API: https://open-meteo.com/en/docs/historical-forecast-api
- IEM API overview: https://www.mesonet.agron.iastate.edu/api/
- IEM ASOS/AWOS/METAR archive: https://www.mesonet.agron.iastate.edu/request/download.phtml
- NOAA HRRR information: https://rapidrefresh.noaa.gov/hrrr/
- NOAA HRRR public data distribution: https://registry.opendata.aws/noaa-hrrr-pds/
