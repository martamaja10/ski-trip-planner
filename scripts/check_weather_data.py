#!/usr/bin/env python3
"""Validate Open-Meteo historical responses for the ski planner.

The current project has one reference row per weather point and stores each
response as a wrapped JSON file under:

    data/raw/open-meteo/<weather_point_id>_<ski-area-slug>/<YYYY-YYYY>.json

The checker compares the saved request with the current reference CSV, checks
the returned model-grid-cell centre and the DEM elevation used at the requested
coordinate, and validates the hourly series. A grid-centre distance warning
is a prompt for spatial review, not a data failure. Snowfall/temperature flags
are diagnostic only. The checker never modifies or removes weather observations
or the raw response files.

Examples (run from the repository root):

    python scripts/check_weather_data.py \
        --resorts-csv data/reference/resorts.csv \
        --data-dir data/raw/open-meteo

    python scripts/check_weather_data.py \
        --resorts-csv data/reference/resorts.csv \
        --data-dir data/raw/open-meteo \
        --season 2021-2022 --season 2022-2023

    python scripts/check_weather_data.py \
        --resorts-csv data/reference/resorts.csv \
        --data-dir data/raw/open-meteo \
        --season 2021-2022 --distance-warning-km 5

    python scripts/check_weather_data.py \
        --resorts-csv data/reference/resorts.csv \
        --data-dir data/raw/open-meteo \
        --weather-point-id 2-4-vallees --season 2023-2024

Exit status is non-zero when a file fails validation or an expected
weather-point/season file is missing.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

VARIABLE_LIMITS = {
    "temperature_2m": (-60.0, 60.0, "°C"),
    "precipitation": (0.0, 500.0, "mm"),
    "snowfall": (0.0, 300.0, "cm"),
    "snow_depth": (0.0, 20.0, "m"),
    "wind_speed_10m": (0.0, 250.0, "km/h"),
}
SNOWFALL_TEMP_WARNING_C = 2.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(a))


def parse_season(value: str) -> tuple[int, str]:
    match = re.fullmatch(r"(\d{4})(?:-(\d{4}))?", str(value).strip())
    if not match:
        raise argparse.ArgumentTypeError("season must be YYYY or YYYY-YYYY+1, e.g. 2023-2024")
    start_year = int(match.group(1))
    end_year = start_year + 1
    if match.group(2) and int(match.group(2)) != end_year:
        raise argparse.ArgumentTypeError(f"season must end in {end_year}")
    return start_year, f"{start_year}-{end_year}"


def season_dates(season_label: str) -> tuple[date, date]:
    start_year = int(season_label[:4])
    return date(start_year, 11, 1), date(start_year + 1, 4, 30)


def expected_hourly_times(start: date, end: date) -> list[datetime]:
    first = datetime.combine(start, datetime.min.time())
    last = datetime.combine(end, datetime.min.time()) + timedelta(hours=23)
    count = int((last - first).total_seconds() // 3600) + 1
    return [first + timedelta(hours=i) for i in range(count)]


def parse_hour(value: str) -> datetime:
    # timezone=UTC responses may contain naive ISO timestamps; interpret as UTC.
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def load_reference(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"weather_point_id", "destination_id", "name", "ski_area_name", "lat", "lon"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Reference CSV is missing columns: {sorted(missing)}")
        rows: dict[str, dict[str, Any]] = {}
        for row in reader:
            point_id = row["weather_point_id"].strip()
            if not point_id:
                raise ValueError("Reference CSV contains a blank weather_point_id")
            if point_id in rows:
                raise ValueError(f"Duplicate weather_point_id in reference CSV: {point_id}")
            row["_lat"] = float(row["lat"])
            row["_lon"] = float(row["lon"])
            for key in ("ski_area_base_alt_m", "ski_area_top_alt_m"):
                try:
                    row[f"_{key}"] = float(row[key]) if row.get(key, "").strip() else None
                except (TypeError, ValueError):
                    row[f"_{key}"] = None
            rows[point_id] = row
    return rows


def identify_point(path: Path, record: dict[str, Any], reference: dict[str, dict[str, Any]]) -> str | None:
    point_id = record.get("weather_point_id")
    if point_id:
        return str(point_id)
    # Current folder convention: <weather_point_id>_<ski-area-slug>.
    for part in reversed(path.parts[:-1]):
        for candidate in sorted(reference, key=len, reverse=True):
            if part == candidate or part.startswith(candidate + "_"):
                return candidate
    return None


def file_matches_weather_point(
    path: Path, point_id: str, reference: dict[str, dict[str, Any]]
) -> bool:
    """Return whether a response file belongs to the selected weather point.

    The record metadata is authoritative when present. The parent-folder
    convention is used as a fallback, including for malformed JSON files so
    that they can still be reported by check_file().
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return identify_point(path, raw, reference) == point_id


def add_issue(errors: list[str], warnings: list[str], severity: str, message: str) -> None:
    (errors if severity == "error" else warnings).append(message)


def check_file(path: Path, reference: dict[str, dict[str, Any]], distance_warning_km: float) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    result: dict[str, Any] = {
        "file": str(path), "weather_point_id": "", "destination": "", "ski_area": "",
        "cell_selection": "",
        "season": path.stem, "status": "PASS", "requested_latitude": "", "requested_longitude": "",
        "returned_latitude": "", "returned_longitude": "", "grid_distance_km": "",
        "grid_location_review": "", "dem_elevation_at_requested_point_m": "",
        "ski_area_base_alt_m": "", "ski_area_top_alt_m": "",
        "dem_elevation_relation_to_ski_area": "", "hourly_records": 0, "expected_hourly_records": 0,
        "start_time_utc": "", "end_time_utc": "", "missing_values": 0,
        "missing_value_pct": 0.0, "snowfall_above_2c_hours": 0,
        "errors": "", "warnings": "",
    }

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        result.update(status="FAIL", errors=f"Could not read valid JSON: {exc}")
        return result
    if not isinstance(raw, dict):
        result.update(status="FAIL", errors="Top-level JSON value is not an object.")
        return result

    point_id = identify_point(path, raw, reference)
    result["weather_point_id"] = point_id or ""
    if not point_id:
        errors.append("Could not identify weather_point_id from record metadata or parent folder.")
        ref = None
    elif point_id not in reference:
        errors.append(f"weather_point_id {point_id!r} is not present in the current reference CSV.")
        ref = None
    else:
        ref = reference[point_id]
        result["destination"] = ref["name"]
        result["ski_area"] = ref["ski_area_name"]
        result["ski_area_base_alt_m"] = ref.get("ski_area_base_alt_m", "")
        result["ski_area_top_alt_m"] = ref.get("ski_area_top_alt_m", "")

    request = raw.get("request") if isinstance(raw.get("request"), dict) else {}
    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    weather_point = raw.get("weather_point") if isinstance(raw.get("weather_point"), dict) else {}
    response = raw.get("response") if isinstance(raw.get("response"), dict) else raw
    # The main archive is intentionally fixed to the elevation-aware land selection.
    # Missing metadata is treated as land for compatibility with the 2023-2024 pilot,
    # which was fetched before cell_selection was recorded explicitly.
    selected_cell = params.get("cell_selection", "land")
    result["cell_selection"] = selected_cell
    if selected_cell != "land":
        errors.append(
            f"Main archive expects cell_selection='land'; found {selected_cell!r}. "
            "Keep nearest experiments under a separate data directory."
        )

    if not isinstance(raw.get("response"), dict) and "hourly" not in raw:
        errors.append("Missing wrapped response or raw Open-Meteo hourly payload.")

    # Validate the saved request against the current CSV: this catches stale cache files.
    if ref is not None:
        try:
            requested_lat = float(params.get("latitude", weather_point.get("latitude")))
            requested_lon = float(params.get("longitude", weather_point.get("longitude")))
            result["requested_latitude"] = requested_lat
            result["requested_longitude"] = requested_lon
            if abs(requested_lat - ref["_lat"]) > 1e-5 or abs(requested_lon - ref["_lon"]) > 1e-5:
                errors.append(
                    "Saved request coordinates do not match the current resorts CSV "
                    f"(saved {requested_lat:.6f}, {requested_lon:.6f}; "
                    f"current {ref['_lat']:.6f}, {ref['_lon']:.6f}). This is likely a stale cache file."
                )
        except (TypeError, ValueError):
            errors.append("Saved request is missing valid latitude/longitude parameters.")

    # Check filename/metadata/request date range and requested variables.
    season_match = re.fullmatch(r"(\d{4})-(\d{4})", path.stem)
    if not season_match or int(season_match.group(2)) != int(season_match.group(1)) + 1:
        warnings.append("Filename is not a season-labelled file such as 2023-2024.json.")
        season_label = None
    else:
        season_label = path.stem
        result["season"] = season_label
    if raw.get("season") and season_label and raw["season"] != season_label:
        errors.append(f"Record season metadata {raw['season']!r} does not match filename {season_label!r}.")
    if season_label:
        start, end = season_dates(season_label)
        if params:
            if params.get("start_date") != start.isoformat() or params.get("end_date") != end.isoformat():
                errors.append(
                    f"Request dates do not match season {season_label}: expected {start} to {end}, "
                    f"got {params.get('start_date')} to {params.get('end_date')}."
                )
        result["expected_hourly_records"] = len(expected_hourly_times(start, end))
    if params.get("timezone") not in (None, "UTC"):
        warnings.append(f"Request timezone is {params.get('timezone')!r}, not UTC.")
    requested_vars = set(str(params.get("hourly", "")).split(","))
    if params and requested_vars and not set(VARIABLE_LIMITS).issubset(requested_vars):
        errors.append(f"Request is missing required hourly variables: {sorted(set(VARIABLE_LIMITS) - requested_vars)}.")

    # Returned grid coordinates and elevation.
    try:
        returned_lat = float(response["latitude"])
        returned_lon = float(response["longitude"])
        result["returned_latitude"] = returned_lat
        result["returned_longitude"] = returned_lon
        if ref is not None:
            distance = haversine_km(ref["_lat"], ref["_lon"], returned_lat, returned_lon)
            result["grid_distance_km"] = round(distance, 3)
            if distance > distance_warning_km:
                result["grid_location_review"] = "REVIEW_REQUIRED"
                warnings.append(
                    f"Returned model-grid cell centre is {distance:.2f} km from the requested point "
                    f"(review threshold {distance_warning_km:.1f} km); review its spatial "
                    "relationship to the ski-area footprint. This is a non-fatal review warning, "
                    "not a data-validation failure."
                )
            else:
                result["grid_location_review"] = "WITHIN_THRESHOLD"
    except (KeyError, TypeError, ValueError):
        errors.append("Response is missing valid returned grid latitude/longitude.")

    try:
        elevation = float(response["elevation"])
        result["dem_elevation_at_requested_point_m"] = elevation
        if ref is not None:
            low, high = ref.get("_ski_area_base_alt_m"), ref.get("_ski_area_top_alt_m")
            if low is not None and high is not None:
                if low <= elevation <= high:
                    result["dem_elevation_relation_to_ski_area"] = "within ski-area altitude range"
                elif elevation < low:
                    result["dem_elevation_relation_to_ski_area"] = f"{low - elevation:.0f} m below ski-area base"
                    warnings.append(
                        f"DEM elevation at the requested point ({elevation:.0f} m) is below "
                        f"the ski-area base ({low:.0f} m)."
                    )
                else:
                    result["dem_elevation_relation_to_ski_area"] = f"{elevation - high:.0f} m above ski-area top"
                    warnings.append(
                        f"DEM elevation at the requested point ({elevation:.0f} m) is above "
                        f"the ski-area top ({high:.0f} m)."
                    )
    except (KeyError, TypeError, ValueError):
        warnings.append("Response has no valid DEM elevation at the requested point.")

    hourly = response.get("hourly")
    if not isinstance(hourly, dict):
        errors.append("Missing or invalid response.hourly object.")
        hourly = {}
    times_raw = hourly.get("time")
    times: list[datetime] = []
    if not isinstance(times_raw, list) or not times_raw:
        errors.append("Missing or empty hourly.time array.")
    else:
        try:
            times = [parse_hour(value) for value in times_raw]
        except (TypeError, ValueError) as exc:
            errors.append(f"Could not parse hourly timestamps: {exc}")
        if times:
            result["hourly_records"] = len(times)
            result["start_time_utc"] = times[0].isoformat()
            result["end_time_utc"] = times[-1].isoformat()
            if times != sorted(times):
                errors.append("Hourly timestamps are not in increasing order.")
            if len(times) != len(set(times)):
                errors.append(f"{len(times) - len(set(times))} duplicate hourly timestamp(s).")
            if season_label:
                expected = expected_hourly_times(*season_dates(season_label))
                if times != expected:
                    errors.append(
                        f"Hourly timestamps do not exactly cover {season_label} "
                        f"({len(expected)} expected records; found {len(times)})."
                    )
            else:
                for previous, current in zip(times, times[1:]):
                    if current - previous != timedelta(hours=1):
                        errors.append("Hourly timestamps contain a gap or irregular interval.")
                        break

    arrays: dict[str, list[Any]] = {}
    units = response.get("hourly_units", {}) if isinstance(response.get("hourly_units"), dict) else {}
    for variable, (minimum, maximum, expected_unit) in VARIABLE_LIMITS.items():
        values = hourly.get(variable)
        if not isinstance(values, list):
            errors.append(f"Missing or invalid hourly.{variable} array.")
            continue
        arrays[variable] = values
        if len(values) != len(times):
            errors.append(f"hourly.{variable} has {len(values)} values but hourly.time has {len(times)}.")
        unit = units.get(variable)
        if unit and unit != expected_unit:
            warnings.append(f"hourly.{variable} unit is {unit!r}; expected {expected_unit!r}.")
        for index, value in enumerate(values):
            if value is None:
                result["missing_values"] += 1
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                errors.append(f"Invalid {variable} value at row {index}: {value!r}.")
                continue
            if value < minimum or value > maximum:
                errors.append(
                    f"{variable} outside broad physical bounds at row {index}: "
                    f"{value} {expected_unit} (allowed {minimum} to {maximum})."
                )

    total_values = sum(len(values) for values in arrays.values())
    if total_values:
        result["missing_value_pct"] = round(100 * result["missing_values"] / total_values, 3)
    if result["missing_values"]:
        warnings.append(
            f"{result['missing_values']} null weather values "
            f"({result['missing_value_pct']:.2f}% of requested values)."
        )

    temp, snow = arrays.get("temperature_2m", []), arrays.get("snowfall", [])
    if len(temp) == len(snow) == len(times):
        suspicious = [i for i, (t, s) in enumerate(zip(temp, snow))
                      if t is not None and s is not None and t > SNOWFALL_TEMP_WARNING_C and s > 0]
        result["snowfall_above_2c_hours"] = len(suspicious)
        if suspicious:
            examples = ", ".join(f"{times[i].isoformat()} ({temp[i]}°C, {snow[i]} cm)" for i in suspicious[:3])
            warnings.append(
                f"{len(suspicious)} hour(s) report snowfall while 2 m temperature exceeds "
                f"{SNOWFALL_TEMP_WARNING_C:.1f}°C; diagnostic only (observations are retained "
                f"unchanged); inspect examples: {examples}."
            )

    depth = arrays.get("snow_depth", [])
    if depth and all(value == 0 for value in depth if value is not None):
        warnings.append("Snow depth is zero for every non-null hourly record; check whether this is plausible.")

    result["errors"] = " | ".join(errors)
    result["warnings"] = " | ".join(warnings)
    result["status"] = "FAIL" if errors else "WARN" if warnings else "PASS"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resorts-csv", type=Path, required=True, help="Current data/reference/resorts.csv")
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw/open-meteo"), help="Root containing weather-point folders")
    parser.add_argument("--season", action="append", default=[], help="Only check this season (YYYY or YYYY-YYYY); repeatable")
    parser.add_argument("--weather-point-id", help="Only check this weather-point ID and its expected season files")
    parser.add_argument("--distance-warning-km", type=float, default=5.0, help="Warn when returned grid cell is farther than this distance (default: 5 km)")
    parser.add_argument("--output-dir", type=Path, default=Path("data/interim/open_meteo_validation"))
    args = parser.parse_args()

    if args.distance_warning_km < 0:
        parser.error("--distance-warning-km must be non-negative")
    try:
        reference = load_reference(args.resorts_csv)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not args.data_dir.exists():
        print(f"ERROR: weather data directory does not exist: {args.data_dir}", file=sys.stderr)
        return 2

    if args.weather_point_id:
        if args.weather_point_id not in reference:
            print(
                f"ERROR: weather_point_id {args.weather_point_id!r} is not present "
                "in the reference CSV.",
                file=sys.stderr,
            )
            return 2
        reference = {args.weather_point_id: reference[args.weather_point_id]}

    requested_seasons = [parse_season(value)[1] for value in args.season]
    files = sorted(args.data_dir.rglob("*.json"))
    if requested_seasons:
        files = [p for p in files if p.stem in requested_seasons]
    if args.weather_point_id:
        files = [
            path for path in files
            if file_matches_weather_point(path, args.weather_point_id, reference)
        ]
    if not files and not requested_seasons:
        print(f"No matching JSON files found under {args.data_dir}.", file=sys.stderr)
        return 2

    results = [check_file(path, reference, args.distance_warning_km) for path in files]
    # Explicitly report missing weather-point/season combinations when seasons were requested.
    missing: list[dict[str, Any]] = []
    if requested_seasons:
        seen = {(r["weather_point_id"], r["season"]) for r in results}
        for point_id, ref in reference.items():
            for season in requested_seasons:
                if (point_id, season) not in seen:
                    missing.append({
                        "file": "", "weather_point_id": point_id, "destination": ref["name"],
                        "ski_area": ref["ski_area_name"], "season": season, "status": "MISSING",
                        "errors": "No response file found for this weather point and season.", "warnings": "",
                    })

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "open_meteo_validation.csv"
    json_path = args.output_dir / "open_meteo_validation.json"
    all_results = results + missing
    fieldnames = list(dict.fromkeys(key for row in all_results for key in row))
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_results)
    summary = Counter(row["status"] for row in all_results)
    payload = {
        "reference_csv": str(args.resorts_csv), "data_dir": str(args.data_dir),
        "distance_warning_km": args.distance_warning_km,
        "field_explanations": {
            "returned_latitude/returned_longitude": (
                "Centre coordinates of the weather-model grid cell used to generate the response; "
                "they may be kilometres from the requested coordinate."
            ),
            "dem_elevation_at_requested_point_m": (
                "Elevation from Open-Meteo's 90 m digital elevation model at the requested "
                "latitude/longitude. It is used for grid-cell selection and statistical "
                "downscaling; it is not necessarily the terrain elevation at the returned "
                "grid-cell centre."
            ),
            "grid_location_review": (
                "REVIEW_REQUIRED means the returned grid-cell centre is beyond the distance "
                "threshold and should be compared with the ski-area footprint. It is a warning, "
                "not a validation failure."
            ),
            "snowfall_above_2c_hours": (
                "Diagnostic count only. These observations are not altered or discarded."
            ),
        },
        "requested_seasons": requested_seasons,
        "weather_points_in_reference": len(reference),
        "weather_point_filter": args.weather_point_id,
        "files_checked": len(results), "summary": dict(summary),
        "results": all_results,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Reference weather points: {len(reference)}")
    if args.weather_point_id:
        print(f"Weather-point filter:     {args.weather_point_id}")
    print(f"Files checked:            {len(results)}")
    print(f"PASS: {summary['PASS']}  WARN: {summary['WARN']}  FAIL: {summary['FAIL']}  MISSING: {summary['MISSING']}")
    print("WARN means review/diagnostic only; only FAIL or MISSING produces a non-zero exit status.")
    print(f"CSV report:  {csv_path}")
    print(f"JSON report: {json_path}")
    for row in all_results:
        if row["status"] in {"FAIL", "MISSING"}:
            print(f"{row['status']}: {row.get('weather_point_id', '')} {row.get('season', '')}: {row.get('errors', '')}")
    return 1 if summary["FAIL"] or summary["MISSING"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
