# Multi-source NFDRS4 integration demonstration

This is an additive integration path for the existing California wildfire project. It does not overwrite the original Open-Meteo historical pipeline, existing NFDRS4 outputs, or raw API archives. New files are isolated under `live/nfdrs_multisource/`.

## Sources and how they enter NFDRS4

| Source | Current archive coverage | NFDRS4 input method | Important limitation |
|---|---:|---|---|
| Open-Meteo GFS | 9 IEM station locations, 384 hourly leads each | Direct conversion of model temperature, RH, precipitation, wind, gust, and solar radiation | Current downloaded file is a 9-location test subset, not the six project-city coordinates or full California catalog. |
| NOAA HRRR | 6 original project coordinates, 6 forecast hours each | Direct conversion; Kelvin and m/s converted to FW21 units; accumulated precipitation differenced into hourly increments | Only a six-hour demo window is present. A short forecast is not a long-range prediction. |
| IEM ASOS | 3 stations with observations in the current archive | Actual ASOS temperature, RH, precipitation, and wind; solar radiation is a clear-sky estimate adjusted by METAR cloud category | ASOS archive does not provide solar radiation in this downloaded file. Solar is explicitly estimated, not observed. Stations are AAT, ACV, and AUN; none is one of the original six SoCal city points. |
| Synoptic | Six selected airport stations near the original project cities, Oct. 1–7, 2026 | Actual Synoptic temperature, RH, and wind; precipitation and solar radiation are joined from the existing Open-Meteo historical city series | This is a hybrid input, not a Synoptic-only run. It is suitable for demonstrating pipeline compatibility, not attributing every input variable to Synoptic. |

NFDRS4's FW21 reader requires DateTime, Temperature(F), RelativeHumidity(%), Precipitation(in), WindSpeed(mph), WindAzimuth(degrees), SolarRadiation(W/m2), and SnowFlag. Missing required fields are not silently replaced with zeroes. The two hybrid cases above are labelled in the manifest and config comments.

## Run on Windows

Open PowerShell in the project folder (the folder containing `integrate_weather_sources_nfdrs.py`):

```powershell
python integrate_weather_sources_nfdrs.py
```

This prepares source-specific `.fw21` files and NFDRS4 config files, and writes `live/nfdrs_multisource/integration_manifest.csv`.

Then run NFDRS4 for every prepared source/location:

```powershell
python integrate_weather_sources_nfdrs.py --run
```

To run selected sources only:

```powershell
python integrate_weather_sources_nfdrs.py --run --sources gfs hrrr
python integrate_weather_sources_nfdrs.py --run --sources iem synoptic
```

The script expects the existing Windows executable at `build/bin/NFDRS4_cli.exe` and initialization file at `data/NFDRSInitSample.txt`. It creates independent outputs under `live/nfdrs_multisource/<source>/<location>/`, preserving the prior NFDRS4 result files.

## Interpretation and research cautions

- The generated NFDRS4 runs demonstrate that source-derived FW21 weather streams can be ingested and processed. They do not establish that NFDRS4 is calibrated for each station or that a fire will ignite.
- These demo runs use the project's sample NFDRS initialization file for each independent source stream. They do not continue fuel-moisture state from the original six-location historical run.
- Compare like-for-like time windows and stations before drawing conclusions about source quality.
- IEM estimated solar and Synoptic/Open-Meteo hybrid fields must remain identified as such in reports and charts.
- NFDRS4 fire-danger indices are indicators of fire environment/fire potential, not direct probabilities of ignition. Ignition prediction requires fire-occurrence labels and a separately validated statistical/ML model.
