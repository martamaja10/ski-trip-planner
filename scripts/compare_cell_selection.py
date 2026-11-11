#!/usr/bin/env python3
"""Compare Open-Meteo land and nearest responses for selected ski weather points.

Example (run from the repository root):

    python scripts/compare_cell_selection.py \
        --resorts-csv data/reference/resorts.csv \
        --season 2023-2024 \
        --land-dir data/raw/open-meteo \
        --nearest-dir data/raw/open-meteo-nearest \
        --weather-point-id 2-4-vallees \
        --weather-point-id 3-gstaad \
        --output-dir data/interim/cell_selection_comparison/2023-2024

The script does not modify either source dataset. It writes a CSV and JSON
comparison report. Run check_weather_data.py separately on each dataset first
(or afterwards) to validate full-season completeness and physical bounds.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VARIABLES = {
    "temperature_2m": "°C",
    "precipitation": "mm",
    "snowfall": "cm",
    "snow_depth": "m",
    "wind_speed_10m": "km/h",
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(a))


def parse_hour(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def load_reference(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"weather_point_id", "lat", "lon", "name", "ski_area_name"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Reference CSV is missing columns: {sorted(missing)}")
        rows: dict[str, dict[str, Any]] = {}
        for row in reader:
            point_id = row["weather_point_id"].strip()
            if not point_id:
                raise ValueError("Reference CSV contains a blank weather_point_id")
            if point_id in rows:
                raise ValueError(f"Duplicate weather_point_id: {point_id}")
            row["_lat"] = float(row["lat"])
            row["_lon"] = float(row["lon"])
            rows[point_id] = row
    return rows


def identify_point(path: Path, record: dict[str, Any], reference: dict[str, dict[str, Any]]) -> str | None:
    point_id = record.get("weather_point_id")
    if point_id:
        return str(point_id)
    for part in reversed(path.parts[:-1]):
        for candidate in sorted(reference, key=len, reverse=True):
            if part == candidate or part.startswith(candidate + "_"):
                return candidate
    return None


def index_files(data_dir: Path, season: str, reference: dict[str, dict[str, Any]]) -> dict[str, Path]:
    indexed: dict[str, Path] = {}
    for path in sorted(data_dir.rglob(f"{season}.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            # Preserve the file in the index if its folder identifies the point;
            # load_record() will report the malformed JSON clearly.
            raw = {}
        point_id = identify_point(path, raw if isinstance(raw, dict) else {}, reference)
        if point_id not in reference:
            continue
        if point_id in indexed:
            raise ValueError(f"Multiple {season} files found for {point_id} under {data_dir}")
        indexed[point_id] = path
    return indexed


def load_record(path: Path, expected_selection: str) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, [f"Cannot read JSON: {exc}"]
    if not isinstance(raw, dict):
        return {}, ["Top-level JSON value is not an object"]

    request = raw.get("request") if isinstance(raw.get("request"), dict) else {}
    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    response = raw.get("response") if isinstance(raw.get("response"), dict) else {}
    if not params:
        issues.append("Missing request.params")
    if not response:
        issues.append("Missing wrapped response object")

    # Legacy pilot files omitted this parameter; that means the documented default, land.
    selection = params.get("cell_selection", "land")
    if selection != expected_selection:
        issues.append(
            f"Expected cell_selection={expected_selection!r}, found {selection!r}"
        )
    return {"raw": raw, "params": params, "response": response}, issues


def numeric_values(values: Any) -> list[float]:
    if not isinstance(values, list):
        return []
    return [float(value) for value in values
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)]


def mean(values: list[float]) -> float | str:
    return round(sum(values) / len(values), 4) if values else ""


def total(values: list[float]) -> float | str:
    return round(sum(values), 4) if values else ""


def maximum(values: list[float]) -> float | str:
    return round(max(values), 4) if values else ""


def get_weather_summary(response: dict[str, Any], prefix: str) -> dict[str, Any]:
    hourly = response.get("hourly") if isinstance(response.get("hourly"), dict) else {}
    output: dict[str, Any] = {}
    reducers = {
        "temperature_2m_mean": ("temperature_2m", mean),
        "precipitation_total": ("precipitation", total),
        "snowfall_total": ("snowfall", total),
        "snow_depth_mean": ("snow_depth", mean),
        "snow_depth_max": ("snow_depth", maximum),
        "wind_speed_10m_mean": ("wind_speed_10m", mean),
    }
    for output_name, (source_variable, reducer) in reducers.items():
        output[f"{prefix}_{output_name}"] = reducer(numeric_values(hourly.get(source_variable)))
    return output


def compare_hourly(land_response: dict[str, Any], nearest_response: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    land_hourly = land_response.get("hourly") if isinstance(land_response.get("hourly"), dict) else {}
    nearest_hourly = nearest_response.get("hourly") if isinstance(nearest_response.get("hourly"), dict) else {}
    land_times_raw, nearest_times_raw = land_hourly.get("time"), nearest_hourly.get("time")
    metrics: dict[str, Any] = {}
    try:
        land_times = [parse_hour(value) for value in land_times_raw]
        nearest_times = [parse_hour(value) for value in nearest_times_raw]
    except (TypeError, ValueError) as exc:
        return metrics, [f"Cannot parse hourly timestamps: {exc}"]
    if land_times != nearest_times:
        issues.append("Hourly timestamps differ between land and nearest responses")
        return metrics, issues
    metrics["hourly_records_compared"] = len(land_times)

    for variable, unit in VARIABLES.items():
        land_values, nearest_values = land_hourly.get(variable), nearest_hourly.get(variable)
        if not isinstance(land_values, list) or not isinstance(nearest_values, list):
            issues.append(f"Missing hourly.{variable} in one or both responses")
            continue
        if len(land_values) != len(land_times) or len(nearest_values) != len(nearest_times):
            issues.append(f"hourly.{variable} length does not match timestamps")
            continue
        differences = []
        for left, right in zip(land_values, nearest_values):
            if (isinstance(left, (int, float)) and not isinstance(left, bool)
                    and isinstance(right, (int, float)) and not isinstance(right, bool)
                    and math.isfinite(left) and math.isfinite(right)):
                differences.append(float(right) - float(left))
        metrics[f"{variable}_paired_hours"] = len(differences)
        metrics[f"{variable}_mean_signed_difference_nearest_minus_land_{unit}"] = mean(differences)
        metrics[f"{variable}_mean_absolute_difference_{unit}"] = mean([abs(v) for v in differences])
        metrics[f"{variable}_max_absolute_difference_{unit}"] = maximum([abs(v) for v in differences])
    return metrics, issues


def compare_point(
    point_id: str,
    ref: dict[str, Any],
    land_path: Path | None,
    nearest_path: Path | None,
    season: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "weather_point_id": point_id,
        "destination": ref.get("name", ""),
        "ski_area": ref.get("ski_area_name", ""),
        "season": season,
        "status": "PASS",
        "land_file": str(land_path) if land_path else "",
        "nearest_file": str(nearest_path) if nearest_path else "",
        "issues": "",
    }
    issues: list[str] = []
    if land_path is None:
        issues.append("Missing land response")
    if nearest_path is None:
        issues.append("Missing nearest response")
    if issues:
        row["status"] = "MISSING"
        row["issues"] = " | ".join(issues)
        return row

    land, land_issues = load_record(land_path, "land")
    nearest, nearest_issues = load_record(nearest_path, "nearest")
    issues.extend(f"land: {issue}" for issue in land_issues)
    issues.extend(f"nearest: {issue}" for issue in nearest_issues)
    if not land or not nearest:
        row["status"] = "FAIL"
        row["issues"] = " | ".join(issues)
        return row

    land_params, nearest_params = land["params"], nearest["params"]
    land_response, nearest_response = land["response"], nearest["response"]
    for method, params in (("land", land_params), ("nearest", nearest_params)):
        try:
            lat, lon = float(params["latitude"]), float(params["longitude"])
            if abs(lat - ref["_lat"]) > 1e-5 or abs(lon - ref["_lon"]) > 1e-5:
                issues.append(f"{method}: requested coordinates do not match reference CSV")
            row[f"{method}_requested_latitude"] = lat
            row[f"{method}_requested_longitude"] = lon
        except (KeyError, TypeError, ValueError):
            issues.append(f"{method}: missing or invalid requested coordinates")
    if (land_params.get("latitude"), land_params.get("longitude")) != (
        nearest_params.get("latitude"), nearest_params.get("longitude")
    ):
        issues.append("Requested coordinates differ between land and nearest requests")
    for key in ("start_date", "end_date", "timezone", "hourly"):
        if land_params.get(key) != nearest_params.get(key):
            issues.append(f"Request parameter {key!r} differs between land and nearest")

    for method, response in (("land", land_response), ("nearest", nearest_response)):
        try:
            lat, lon = float(response["latitude"]), float(response["longitude"])
            distance = haversine_km(ref["_lat"], ref["_lon"], lat, lon)
            row[f"{method}_returned_latitude"] = lat
            row[f"{method}_returned_longitude"] = lon
            row[f"{method}_grid_distance_km"] = round(distance, 3)
        except (KeyError, TypeError, ValueError):
            issues.append(f"{method}: missing or invalid returned grid-cell coordinates")
        try:
            row[f"{method}_dem_elevation_at_requested_point_m"] = float(response["elevation"])
        except (KeyError, TypeError, ValueError):
            row[f"{method}_dem_elevation_at_requested_point_m"] = ""
            issues.append(f"{method}: missing or invalid DEM elevation")

    if "land_grid_distance_km" in row and "nearest_grid_distance_km" in row:
        row["distance_improvement_km_land_minus_nearest"] = round(
            row["land_grid_distance_km"] - row["nearest_grid_distance_km"], 3
        )
    if "land_dem_elevation_at_requested_point_m" in row and "nearest_dem_elevation_at_requested_point_m" in row:
        row["dem_elevation_difference_m_nearest_minus_land"] = round(
            row["nearest_dem_elevation_at_requested_point_m"] - row["land_dem_elevation_at_requested_point_m"], 3
        )

    row.update(get_weather_summary(land_response, "land"))
    row.update(get_weather_summary(nearest_response, "nearest"))
    hourly_metrics, hourly_issues = compare_hourly(land_response, nearest_response)
    row.update(hourly_metrics)
    issues.extend(hourly_issues)

    if issues:
        row["status"] = "FAIL"
        row["issues"] = " | ".join(issues)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resorts-csv", type=Path, required=True)
    parser.add_argument("--season", required=True, help="Season label, e.g. 2023-2024")
    parser.add_argument("--land-dir", type=Path, default=Path("data/raw/open-meteo"))
    parser.add_argument("--nearest-dir", type=Path, default=Path("data/raw/open-meteo-nearest"))
    parser.add_argument("--weather-point-id", action="append", required=True,
                        help="Weather point to compare; repeat for each point")
    parser.add_argument("--output-dir", type=Path, default=Path("data/interim/cell_selection_comparison"))
    args = parser.parse_args()

    if not re.fullmatch(r"\d{4}-\d{4}", args.season) or int(args.season[5:]) != int(args.season[:4]) + 1:
        parser.error("--season must be a label such as 2023-2024")
    try:
        reference = load_reference(args.resorts_csv)
        requested_ids = list(dict.fromkeys(args.weather_point_id))
        unknown = sorted(set(requested_ids) - set(reference))
        if unknown:
            raise ValueError(f"Unknown weather_point_id(s): {', '.join(unknown)}")
        land_index = index_files(args.land_dir, args.season, reference)
        nearest_index = index_files(args.nearest_dir, args.season, reference)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    results = [
        compare_point(point_id, reference[point_id], land_index.get(point_id), nearest_index.get(point_id), args.season)
        for point_id in requested_ids
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "cell_selection_comparison.csv"
    json_path = args.output_dir / "cell_selection_comparison.json"
    fieldnames = list(dict.fromkeys(key for result in results for key in result))
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    summary = dict(Counter(result["status"] for result in results))
    payload = {
        "season": args.season,
        "land_data_dir": str(args.land_dir),
        "nearest_data_dir": str(args.nearest_dir),
        "comparison_scope": requested_ids,
        "summary": summary,
        "interpretation_notes": [
            "Positive distance_improvement_km_land_minus_nearest means nearest selected a grid-cell centre closer to the requested point.",
            "Returned latitude/longitude are grid-cell centre coordinates; distance alone does not establish ski-terrain representativeness.",
            "DEM elevation is reported by Open-Meteo for the requested coordinate, not necessarily the returned grid-cell centre.",
            "Weather differences are nearest minus land for signed differences; absolute-difference columns show magnitude only.",
            "This report compares responses; it does not select a preferred method or modify either dataset.",
        ],
        "results": results,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Points compared: {len(results)}")
    print("Status: " + ", ".join(f"{key}={value}" for key, value in sorted(summary.items())))
    print(f"CSV report:  {csv_path}")
    print(f"JSON report: {json_path}")
    for result in results:
        if result["status"] != "PASS":
            print(f"{result['status']}: {result['weather_point_id']}: {result['issues']}")
    return 1 if any(result["status"] != "PASS" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
