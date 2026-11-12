#!/usr/bin/env python3
"""Read-only, source-aware NFDRS4 FW21 validation (not a calibration).

Run: python validate_nfdrs_sources.py
Reads existing manifest and FW21 files; writes reports to
live/nfdrs_source_validation/. Never modifies input data.
"""
import csv
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / 'live/nfdrs_multisource/integration_manifest.csv'
OUT = ROOT / 'live/nfdrs_source_validation'
COLUMNS = ['DateTime', 'Temperature(F)', 'RelativeHumidity(%)', 'Precipitation(in)',
           'WindSpeed(mph)', 'WindAzimuth(degrees)', 'SolarRadiation(W/m2)']
BOUNDS = {'Temperature(F)': (-100, 150), 'RelativeHumidity(%)': (0, 100),
          'Precipitation(in)': (0, 50), 'WindSpeed(mph)': (0, 200),
          'WindAzimuth(degrees)': (0, 360), 'SolarRadiation(W/m2)': (0, 1500)}
ISSUE_COLS = ['source', 'station_id', 'location', 'severity', 'check', 'row_number', 'timestamp', 'detail']
SUMMARY_COLS = ['source', 'station_id', 'location', 'status', 'rows', 'weather_file',
                'first_utc', 'last_utc', 'gap_count', 'largest_gap_hours',
                'positive_precip_rows', 'precip_sum_inches_unverified',
                'errors', 'reviews', 'notes', 'checks']


def read_csv(path):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def locate(path):
    p = Path(str(path).strip().strip('"').replace('\\', '/'))
    return p if p.is_absolute() else ROOT / p


def numeric(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def utc(value):
    try:
        d = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
        if d.tzinfo is None:
            # FW21 project convention; confirm against original data provider.
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def source_notes(source):
    if source == 'open_meteo_gfs':
        return [
            ('forecast_provenance', 'GFS forecast, not station observations; confirm forecast cycle and lead times.'),
            ('precipitation_semantics', 'Verify Open-Meteo precipitation is hourly interval precipitation in inches after conversion; do not difference blindly.'),
            ('unit_conversion', 'Confirm temperature F, wind mph, precipitation inches, solar W/m2 against original Open-Meteo request and response.'),
        ]
    if source == 'noaa_hrrr':
        return [
            ('forecast_provenance', 'HRRR forecast, not station observations; verify valid time versus initialization and lead hour.'),
            ('precipitation_semantics', 'Verify APCP accumulation interval and lead-hour differencing, including forecast-cycle boundaries and resets.'),
            ('unit_conversion', 'Confirm K to F, m/s to mph, kg/m2 to inches, and radiation field units against raw GRIB metadata.'),
        ]
    if source == 'iem_asos':
        return [
            ('observation_cadence', 'ASOS observations are irregular; gaps >90 minutes are screening flags, not proof of missing scheduled hourly observations.'),
            ('estimated_solar', 'Solar radiation is estimated rather than measured; validate estimate before scientific use.'),
            ('precipitation_semantics', 'Verify METAR precipitation accumulation period, trace handling, and any unit conversions; do not assume every value is hourly.'),
        ]
    if source == 'synoptic_hybrid':
        return [
            ('hybrid_provenance', 'Temperature/RH/wind observed; precipitation and solar are modeled in this existing hybrid stream.'),
            ('precipitation_semantics', 'Verify modeled hourly precipitation and units; do not substitute raw Synoptic accumulation counters.'),
            ('station_identity', 'Verify reference coordinates and station identity: city reference points may differ from named airport station coordinates.'),
        ]
    return [('unknown_source', 'No source-specific verification rules defined; inspect source provenance and units.')]


def main():
    if not MANIFEST.is_file():
        raise SystemExit(f'Missing integration manifest: {MANIFEST}')
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = read_csv(MANIFEST)
    findings, summaries = [], []
    for item in manifest:
        source, station, location = (item.get(k, '') for k in ('source', 'station_id', 'location'))
        path_text = item.get('weather_file', '')
        path = locate(path_text) if path_text else None
        local = []

        def flag(severity, check, detail, row='', timestamp=''):
            record = dict(source=source, station_id=station, location=location,
                          severity=severity, check=check, row_number=row,
                          timestamp=timestamp, detail=detail)
            local.append(record)
            findings.append(record)

        rows, first, last, gaps, largest, precip_count, precip_sum = 0, None, None, 0, 0., 0, 0.
        if path is None or not path.is_file():
            flag('FAIL', 'file_missing', f'Missing weather file: {path_text}')
        else:
            try:
                with path.open('r', encoding='utf-8-sig', newline='') as f:
                    reader = csv.DictReader(f)
                    headers = reader.fieldnames or []
                    missing = [c for c in COLUMNS if c not in headers]
                    if missing:
                        flag('FAIL', 'required_columns_missing', ', '.join(missing))
                    prev = None
                    seen = set()
                    for lineno, record in enumerate(reader, 2):
                        rows += 1
                        stamp = record.get('DateTime', '')
                        dt = utc(stamp)
                        if dt is None:
                            flag('FAIL', 'invalid_timestamp', f'Invalid UTC timestamp: {stamp!r}', lineno, stamp)
                        else:
                            first = dt if first is None else min(first, dt)
                            last = dt if last is None else max(last, dt)
                            if dt in seen:
                                flag('FAIL', 'duplicate_timestamp', 'Duplicate timestamp', lineno, stamp)
                            seen.add(dt)
                            if prev is not None:
                                delta = (dt - prev).total_seconds() / 3600
                                if delta <= 0:
                                    flag('FAIL', 'non_increasing_time', f'Elapsed hours={delta:.3f}', lineno, stamp)
                                elif delta > 1.5:
                                    gaps += 1
                                    largest = max(largest, delta)
                                    if source == 'iem_asos':
                                        flag('REVIEW', 'irregular_asos_gap', f'{delta:.3f} hours; verify expected cadence', lineno, stamp)
                                    else:
                                        flag('REVIEW', 'timestamp_gap', f'{delta:.3f} hours', lineno, stamp)
                            prev = dt
                        for col in COLUMNS[1:]:
                            if col not in headers:
                                continue
                            value = numeric(record.get(col))
                            if value is None:
                                flag('FAIL', 'missing_or_invalid_number', col, lineno, stamp)
                                continue
                            low, high = BOUNDS[col]
                            if not low <= value <= high:
                                flag('FAIL', 'out_of_range', f'{col}={value:g} outside [{low},{high}]', lineno, stamp)
                            if col == 'Precipitation(in)':
                                if value > 0:
                                    precip_count += 1
                                    precip_sum += value
                                if value > 2:
                                    flag('REVIEW', 'large_precip_value', f'{value:g} in; verify interval and units', lineno, stamp)
                    if not rows:
                        flag('FAIL', 'empty_file', 'No weather records')
                    expected = numeric(item.get('rows'))
                    if expected is not None and rows != expected:
                        flag('REVIEW', 'manifest_count_mismatch', f'Manifest={expected:g}; actual={rows}')
            except (OSError, UnicodeError, csv.Error) as exc:
                flag('FAIL', 'unreadable_file', f'{type(exc).__name__}: {exc}')
        for check, detail in source_notes(source):
            flag('REVIEW', check, detail)
        if rows < 720:
            flag('REVIEW', 'short_input_history', f'{rows} rows; fewer than 720 hourly records (30-day screening heuristic)')
        # Passing numerical checks is NOT source provenance verification or NFDRS4 calibration.
        status = 'FAIL' if any(f['severity'] == 'FAIL' for f in local) else 'REVIEW' if local else 'PASS'
        summaries.append(dict(source=source, station_id=station, location=location,
                              status=status, rows=rows, weather_file=path_text,
                              first_utc=first.isoformat() if first else '',
                              last_utc=last.isoformat() if last else '',
                              gap_count=gaps, largest_gap_hours=round(largest, 3),
                              positive_precip_rows=precip_count,
                              precip_sum_inches_unverified=round(precip_sum, 5),
                              errors=sum(f['severity'] == 'FAIL' for f in local),
                              reviews=sum(f['severity'] == 'REVIEW' for f in local),
                              notes='Not calibrated; provider units/precipitation semantics not independently verified',
                              checks='; '.join(sorted(set(f['check'] for f in local)))))
        print(f'{source:18} {station:20} {status:6} {rows:5} rows | {len(local)} findings')
    for filename, columns, values in [('station_validation.csv', SUMMARY_COLS, summaries),
                                      ('validation_findings.csv', ISSUE_COLS, findings)]:
        with (OUT / filename).open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(values)
    print(f'\nChecked {len(summaries)} manifest streams; {len(findings)} findings.')
    print(f'Reports: {OUT}')
    print('REVIEW means source conventions require independent confirmation, not a failed NFDRS4 run.')
    print('No weather inputs or NFDRS4 configuration files were changed.')


if __name__ == '__main__':
    main()
