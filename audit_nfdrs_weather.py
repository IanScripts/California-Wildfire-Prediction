#!/usr/bin/env python3
"""Read-only QC audit for NFDRS4 multisource FW21 weather streams.

Run from the project root: python audit_nfdrs_weather.py
Only writes reports under live/nfdrs_weather_audit/.
"""
import csv
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / 'live' / 'nfdrs_multisource' / 'integration_manifest.csv'
VALIDATION = ROOT / 'live' / 'nfdrs_station_checks' / 'station_validation_report.csv'
OUT = ROOT / 'live' / 'nfdrs_weather_audit'
EXPECTED = ['DateTime', 'Temperature(F)', 'RelativeHumidity(%)', 'Precipitation(in)',
            'WindSpeed(mph)', 'WindAzimuth(degrees)', 'SolarRadiation(W/m2)',
            'SnowFlag', 'GustSpeed(mph)', 'GustAzimuth(degrees)']
FIELDS = ['source', 'location', 'station_id', 'manifest_rows', 'actual_rows', 'weather_file',
          'file_exists', 'status', 'start_time', 'end_time', 'duplicate_timestamps',
          'non_increasing_timestamps', 'gaps_over_90_minutes', 'largest_gap_hours',
          'missing_timestamps', 'missing_required_values', 'invalid_numeric_values',
          'out_of_range_values', 'negative_precipitation', 'positive_precipitation_hours',
          'precipitation_sum_inches', 'warnings_count', 'findings']
ISSUE_FIELDS = ['source', 'location', 'station_id', 'weather_file', 'row_number', 'timestamp',
                'severity', 'check', 'detail']

def resolve_file(value):
    p = Path((value or '').strip().strip('"').replace('\\', '/'))
    return p if p.is_absolute() else ROOT / p

def read_csv(path):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def parse_time(s):
    s = (s or '').strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        return None

def number(s):
    if s is None or not str(s).strip():
        return None
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except ValueError:
        return None

def main():
    if not MANIFEST.is_file():
        raise SystemExit(f'Missing manifest: {MANIFEST}')
    manifest = read_csv(MANIFEST)
    validation = read_csv(VALIDATION) if VALIDATION.is_file() else []
    validation_lookup = {(r.get('source', ''), r.get('location', '')): r for r in validation}
    OUT.mkdir(parents=True, exist_ok=True)
    summaries, issues = [], []
    for entry in manifest:
        source = entry.get('source', '')
        location = entry.get('location', '')
        station = entry.get('station_id', '')
        file_value = entry.get('weather_file', '')
        weather_path = resolve_file(file_value) if file_value else None
        findings = set()
        counts = Counter()
        def issue(check, detail, severity='REVIEW', row='', stamp=''):
            counts[check] += 1
            findings.add(check)
            issues.append(dict(source=source, location=location, station_id=station,
                               weather_file=file_value, row_number=row, timestamp=stamp,
                               severity=severity, check=check, detail=detail))
        summary = dict.fromkeys(FIELDS, '')
        summary.update(source=source, location=location, station_id=station,
                       manifest_rows=entry.get('rows', ''), weather_file=file_value,
                       status=entry.get('status', ''), file_exists='NO', actual_rows=0)
        if not file_value or not weather_path or not weather_path.is_file():
            issue('missing_weather_file', 'Manifest weather_file is empty or file does not exist', 'ERROR')
        else:
            summary['file_exists'] = 'YES'
            try:
                rows = read_csv(weather_path)
                with weather_path.open('r', encoding='utf-8-sig', newline='') as f:
                    header = next(csv.reader(f), [])
            except (OSError, UnicodeError, csv.Error) as e:
                issue('unreadable_weather_file', f'{type(e).__name__}: {e}', 'ERROR')
                rows, header = [], []
            summary['actual_rows'] = len(rows)
            absent = [col for col in EXPECTED[:7] if col not in header]
            if absent:
                issue('missing_required_columns', ', '.join(absent), 'ERROR')
            for col in EXPECTED[7:]:
                if col not in header:
                    issue('missing_optional_column', col)
            manifest_count = number(entry.get('rows', ''))
            if manifest_count is not None and manifest_count != len(rows):
                issue('row_count_mismatch', f'Manifest={manifest_count:g}, file={len(rows)}')
            seen, previous = set(), None
            first, last = None, None
            largest_gap = 0.0
            precip_sum, precip_hours = 0.0, 0
            for i, row in enumerate(rows, start=2):
                stamp = row.get('DateTime', '')
                dt = parse_time(stamp)
                if dt is None:
                    issue('missing_or_invalid_timestamp', f'Unparseable DateTime: {stamp!r}', 'ERROR', i, stamp)
                else:
                    if first is None:
                        first = dt
                    last = dt
                    if dt in seen:
                        issue('duplicate_timestamp', 'Repeated DateTime', 'ERROR', i, stamp)
                    seen.add(dt)
                    if previous is not None:
                        delta = (dt - previous).total_seconds() / 3600
                        if delta <= 0:
                            issue('non_increasing_timestamp', f'Elapsed hours={delta:.3f}', 'ERROR', i, stamp)
                        elif delta > 1.5:
                            largest_gap = max(largest_gap, delta)
                            issue('gap_over_90_minutes', f'Elapsed hours={delta:.3f}', 'REVIEW', i, stamp)
                    previous = dt
                required = EXPECTED[1:7]
                for col in required:
                    if col not in header:
                        continue
                    raw = row.get(col)
                    if raw is None or not str(raw).strip():
                        issue('missing_required_value', col, 'ERROR', i, stamp)
                        continue
                    v = number(raw)
                    if v is None:
                        issue('invalid_numeric_value', f'{col}={raw!r}', 'ERROR', i, stamp)
                        continue
                    bounds = {'Temperature(F)': (-100, 150), 'RelativeHumidity(%)': (0, 100),
                              'Precipitation(in)': (0, 50), 'WindSpeed(mph)': (0, 200),
                              'WindAzimuth(degrees)': (0, 360), 'SolarRadiation(W/m2)': (0, 1500)}
                    lo, hi = bounds[col]
                    if not lo <= v <= hi:
                        issue('out_of_range_value', f'{col}={v:g} outside [{lo},{hi}]', 'ERROR', i, stamp)
                    if col == 'Precipitation(in)':
                        if v < 0:
                            issue('negative_precipitation', f'{v:g} inches', 'ERROR', i, stamp)
                        elif v > 0:
                            precip_hours += 1
                            precip_sum += v
                            if v > 2:
                                issue('high_hourly_precipitation', f'{v:g} inches; verify precipitation semantics', 'REVIEW', i, stamp)
                for col in ('GustSpeed(mph)', 'GustAzimuth(degrees)'):
                    if col in header and str(row.get(col, '')).strip() and number(row[col]) is None:
                        issue('invalid_optional_numeric_value', f'{col}={row[col]!r}', 'REVIEW', i, stamp)
            summary.update(start_time=first.isoformat() if first else '',
                           end_time=last.isoformat() if last else '',
                           duplicate_timestamps=counts['duplicate_timestamp'],
                           non_increasing_timestamps=counts['non_increasing_timestamp'],
                           gaps_over_90_minutes=counts['gap_over_90_minutes'],
                           largest_gap_hours=round(largest_gap, 3),
                           missing_timestamps=counts['missing_or_invalid_timestamp'],
                           missing_required_values=counts['missing_required_value'],
                           invalid_numeric_values=counts['invalid_numeric_value'],
                           out_of_range_values=counts['out_of_range_value'],
                           negative_precipitation=counts['negative_precipitation'],
                           positive_precipitation_hours=precip_hours,
                           precipitation_sum_inches=round(precip_sum, 4))
            if len(rows) < 24 * 30:
                issue('short_weather_history', f'{len(rows)} records; less than 30 days of hourly spin-up')
            if source == 'synoptic_hybrid':
                issue('hybrid_modeled_variables', 'Manifest: temperature/RH/wind observed; precipitation/solar modeled')
            if source == 'iem_asos':
                issue('estimated_solar_radiation', 'Manifest: solar radiation estimated from sky/cloud information')
            if source in ('open_meteo_gfs', 'noaa_hrrr'):
                issue('forecast_input', 'Forecast data; verify precipitation accumulation and lead-hour conventions')
        match = validation_lookup.get((source, location))
        if match:
            if (match.get('errors') or '').strip():
                issue('existing_station_validation_errors', match['errors'], 'ERROR')
            if (match.get('warnings') or '').strip():
                issue('existing_station_validation_warnings', match['warnings'])
        else:
            issue('validation_row_unmatched', 'No exact source/location match in station validation report')
        summary['warnings_count'] = len([x for x in issues if x['source'] == source and x['location'] == location and x['station_id'] == station])
        summary['findings'] = '; '.join(sorted(findings))
        summaries.append(summary)
        print(f"{source:18} {station:20} {summary['actual_rows']:5} rows  {summary['warnings_count']:3} findings")
    for filename, fields, data in [('stream_summary.csv', FIELDS, summaries),
                                   ('detailed_findings.csv', ISSUE_FIELDS, issues)]:
        with (OUT / filename).open('w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(data)
    print(f'\nAudited {len(summaries)} streams. Logged {len(issues)} findings.')
    print(f'Summary: {OUT / "stream_summary.csv"}')
    print(f'Details: {OUT / "detailed_findings.csv"}')
    print('Note: Values and precipitation semantics require source-specific verification; no inputs were changed.')

if __name__ == '__main__':
    main()
