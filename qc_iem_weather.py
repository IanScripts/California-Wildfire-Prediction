"""Read-only quality control for IEM ASOS FW21 weather streams.

Run from any directory: python qc_iem_weather.py
Place this file in the NFDRS4 project root beside the live/ directory.
"""
import csv
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / 'live' / 'nfdrs_multisource' / 'integration_manifest.csv'
OUT = ROOT / 'live' / 'nfdrs_weather_audit' / 'iem_qc'
EXPECTED_HOURS = 1.0
GAP_THRESHOLD_HOURS = 1.5
MIN_CONTINUOUS_HOURS = 24 * 30  # reporting threshold, not a guarantee of adequate NFDRS4 spin-up


def parse_utc(value):
    s = (value or '').strip()
    if not s:
        raise ValueError('empty timestamp')
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError('timestamp lacks timezone')
    return dt.astimezone(timezone.utc)


def write_csv(path, fieldnames, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def resolve_weather_path(value):
    p = Path(value.replace('\\', '/'))
    if p.is_absolute():
        return p
    return ROOT / p


def main():
    if not MANIFEST.is_file():
        raise SystemExit(f'Manifest not found: {MANIFEST}')
    with MANIFEST.open(newline='', encoding='utf-8-sig') as f:
        entries = [r for r in csv.DictReader(f) if r.get('source', '').strip() == 'iem_asos']
    if not entries:
        raise SystemExit('No iem_asos entries in manifest.')
    OUT.mkdir(parents=True, exist_ok=True)
    summaries, gaps, segments, issues = [], [], [], []
    for entry in entries:
        station = entry.get('station_id', '').strip()
        source_path = resolve_weather_path(entry.get('weather_file', '').strip())
        if not source_path.is_file():
            issues.append(dict(station_id=station, weather_file=str(source_path), row_number='', issue='missing_file', detail=''))
            print(f'[MISSING] {station}: {source_path}')
            continue
        with source_path.open(newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or 'DateTime' not in reader.fieldnames:
                issues.append(dict(station_id=station, weather_file=str(source_path), row_number='', issue='missing_datetime_column', detail=''))
                continue
            valid = []
            total = 0
            for line_no, row in enumerate(reader, 2):
                if not any((v or '').strip() for v in row.values() if isinstance(v, str)):
                    continue
                total += 1
                try:
                    dt = parse_utc(row.get('DateTime'))
                    valid.append((dt, line_no))
                except (ValueError, TypeError) as exc:
                    issues.append(dict(station_id=station, weather_file=str(source_path), row_number=line_no, issue='invalid_timestamp', detail=str(exc)))
        valid.sort()
        if not valid:
            issues.append(dict(station_id=station, weather_file=str(source_path), row_number='', issue='no_valid_timestamps', detail=''))
            continue
        start = valid[0][0]
        prev = valid[0][0]
        seg_first = start
        seg_records = 1
        gap_count = 0
        for dt, line_no in valid[1:]:
            elapsed = (dt - prev).total_seconds() / 3600
            if elapsed <= 0:
                issues.append(dict(station_id=station, weather_file=str(source_path), row_number=line_no, issue='duplicate_timestamp', detail=dt.isoformat()))
                continue
            if elapsed > GAP_THRESHOLD_HOURS:
                gap_count += 1
                gaps.append(dict(station_id=station, weather_file=str(source_path), gap_start_utc=prev.isoformat(), gap_end_utc=dt.isoformat(), elapsed_hours=round(elapsed, 5), estimated_missing_hourly_slots=max(0, round(elapsed) - 1), ending_file_row=line_no))
                segments.append(dict(station_id=station, start_utc=seg_first.isoformat(), end_utc=prev.isoformat(), observed_records=seg_records, span_hours=round((prev-seg_first).total_seconds()/3600, 3), at_least_30_days='YES' if (prev-seg_first).total_seconds() >= MIN_CONTINUOUS_HOURS*3600 else 'NO'))
                seg_first = dt
                seg_records = 1
            else:
                seg_records += 1
                if abs(elapsed - EXPECTED_HOURS) > 0.01:
                    issues.append(dict(station_id=station, weather_file=str(source_path), row_number=line_no, issue='irregular_interval_under_gap_threshold', detail=f'{elapsed:.5f} hours'))
            prev = dt
        segments.append(dict(station_id=station, start_utc=seg_first.isoformat(), end_utc=prev.isoformat(), observed_records=seg_records, span_hours=round((prev-seg_first).total_seconds()/3600, 3), at_least_30_days='YES' if (prev-seg_first).total_seconds() >= MIN_CONTINUOUS_HOURS*3600 else 'NO'))
        station_gaps = [g for g in gaps if g['station_id'] == station]
        station_segments = [s for s in segments if s['station_id'] == station]
        summaries.append(dict(station_id=station, weather_file=str(source_path), observed_rows=total, valid_timestamp_rows=len(valid), first_utc=start.isoformat(), last_utc=prev.isoformat(), gaps_over_90_minutes=len(station_gaps), estimated_missing_hourly_slots=sum(g['estimated_missing_hourly_slots'] for g in station_gaps), longest_gap_hours=max((g['elapsed_hours'] for g in station_gaps), default=0), longest_continuous_segment_hours=max((s['span_hours'] for s in station_segments), default=0), segments_at_least_30_days=sum(s['at_least_30_days']=='YES' for s in station_segments)))
        print(f"{station}: {len(valid)} timestamped rows; {gap_count} gaps; longest continuous segment {summaries[-1]['longest_continuous_segment_hours']} hours")
    write_csv(OUT/'station_summary.csv', ['station_id','weather_file','observed_rows','valid_timestamp_rows','first_utc','last_utc','gaps_over_90_minutes','estimated_missing_hourly_slots','longest_gap_hours','longest_continuous_segment_hours','segments_at_least_30_days'], summaries)
    write_csv(OUT/'gap_details.csv', ['station_id','weather_file','gap_start_utc','gap_end_utc','elapsed_hours','estimated_missing_hourly_slots','ending_file_row'], gaps)
    write_csv(OUT/'continuous_segments.csv', ['station_id','start_utc','end_utc','observed_records','span_hours','at_least_30_days'], segments)
    write_csv(OUT/'timestamp_issues.csv', ['station_id','weather_file','row_number','issue','detail'], issues)
    print(f'\nRead-only QC complete. Reports: {OUT}')
    print('No weather files or NFDRS4 configurations were modified.')
    print('NOTE: 30-day segment threshold is a screening heuristic, not proof of valid model initialization.')


if __name__ == '__main__':
    main()
