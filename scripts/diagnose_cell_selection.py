#!/usr/bin/env python3
"""Create spatial maps and hourly-weather diagnostics for land vs nearest.

Run from the ski-planner repository root. The script is read-only with respect
 to the source JSON/CSV files. It writes plots and diagnostic reports only to
 the chosen output directory.

Optional GeoJSON overlays are deliberately generic: all geometries intersecting
 the local map window are drawn. No feature-name matching or resort attribution
 is inferred from GeoJSON properties.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

VARIABLES = {
    "temperature_2m": "°C",
    "precipitation": "mm",
    "snowfall": "cm",
    "snow_depth": "m",
    "wind_speed_10m": "km/h",
}
DEFAULT_POINT_IDS = [
    "2-4-vallees", "3-gstaad", "6-saas-fee",
    "7-kleine-scheidegg-maennlichen", "13-brevent-flegere",
    "13-grands-montets", "16-courmayeur",
]


def parse_time(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def load_reference(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"weather_point_id", "lat", "lon", "name", "ski_area_name"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Reference CSV missing columns: {sorted(missing)}")
        for row in reader:
            point_id = row["weather_point_id"].strip()
            if not point_id:
                continue
            row["_lat"] = float(row["lat"])
            row["_lon"] = float(row["lon"])
            rows[point_id] = row
    return rows


def resolve_report_path(value: str, project_root: Path, fallback_dir: Path | None,
                        point_id: str, season: str) -> Path:
    candidate = Path(value) if value else None
    if candidate is not None:
        if not candidate.is_absolute():
            candidate = project_root / candidate
        if candidate.exists():
            return candidate
    if fallback_dir is not None:
        matches = list(fallback_dir.rglob(f"{season}.json"))
        for path in matches:
            if point_id in path.parts[-2] or path.parent.name.startswith(point_id + "_"):
                return path
    raise FileNotFoundError(
        f"Cannot locate {season}.json for {point_id}. Checked report path {value!r} "
        f"and fallback directory {fallback_dir}"
    )


def get_wrapped_response(path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw = read_json(path)
    request = raw.get("request") if isinstance(raw.get("request"), dict) else {}
    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    response = raw.get("response") if isinstance(raw.get("response"), dict) else {}
    if not response:
        raise ValueError(f"Missing wrapped response object in {path}")
    return raw, params, response


def numeric_series(values: Any, expected_len: int | None = None) -> list[float | None]:
    if not isinstance(values, list):
        return []
    result: list[float | None] = []
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            result.append(float(value))
        else:
            result.append(None)
    if expected_len is not None and len(result) != expected_len:
        raise ValueError(f"Series length {len(result)} != timestamp count {expected_len}")
    return result


def daily_aggregate(times: list[datetime], values: list[float | None], mode: str) -> tuple[list[datetime], list[float | None]]:
    buckets: dict[Any, list[float]] = defaultdict(list)
    for timestamp, value in zip(times, values):
        if value is not None:
            buckets[timestamp.date()].append(value)
    dates = sorted(buckets)
    output: list[float | None] = []
    for date in dates:
        vals = buckets[date]
        if mode == "sum":
            output.append(sum(vals))
        elif mode == "max":
            output.append(max(vals))
        else:
            output.append(sum(vals) / len(vals))
    return [datetime.combine(d, datetime.min.time()) for d in dates], output


def consecutive_hours_above(values: list[float | None], threshold: float) -> int:
    longest = current = 0
    for value in values:
        if value is not None and value > threshold:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest



def hourly_difference_summary(land_hourly: dict[str, Any], nearest_hourly: dict[str, Any]) -> dict[str, Any]:
    """Summarise paired hourly differences; missing pairs are excluded."""
    output: dict[str, Any] = {}
    times = land_hourly.get("time", [])
    for variable, unit in VARIABLES.items():
        left = numeric_series(land_hourly.get(variable), len(times))
        right = numeric_series(nearest_hourly.get(variable), len(times))
        diffs = [b - a for a, b in zip(left, right) if a is not None and b is not None]
        output[variable] = {
            "paired_hours": len(diffs),
            "mean_signed_nearest_minus_land": round(sum(diffs) / len(diffs), 5) if diffs else None,
            "mean_absolute_difference": round(sum(abs(x) for x in diffs) / len(diffs), 5) if diffs else None,
            "max_absolute_difference": round(max(abs(x) for x in diffs), 5) if diffs else None,
            "unit": unit,
            "identical": bool(diffs) and all(abs(x) < 1e-12 for x in diffs),
        }
    return output


def request_differences(land_params: dict[str, Any], nearest_params: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(set(land_params) | set(nearest_params))
    return {
        key: {"land": land_params.get(key), "nearest": nearest_params.get(key)}
        for key in keys
        if land_params.get(key) != nearest_params.get(key)
        and key != "cell_selection"
    }


def response_metadata(response: dict[str, Any]) -> dict[str, Any]:
    # Exclude generationtime_ms: it is request execution time, not weather metadata.
    keys = ("latitude", "longitude", "elevation", "timezone", "utc_offset_seconds",
            "timezone_abbreviation")
    return {key: response.get(key) for key in keys if key in response}


def extract_geometry_parts(geometry: dict[str, Any] | None) -> Iterable[tuple[str, list[Any]]]:
    if not isinstance(geometry, dict):
        return
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if kind in {"LineString", "MultiPoint"} and isinstance(coords, list):
        yield kind, coords
    elif kind in {"MultiLineString", "Polygon"} and isinstance(coords, list):
        for part in coords:
            if isinstance(part, list):
                yield "LineString", part
    elif kind == "MultiPolygon" and isinstance(coords, list):
        for polygon in coords:
            if isinstance(polygon, list):
                for ring in polygon:
                    if isinstance(ring, list):
                        yield "LineString", ring
    elif kind == "Point" and isinstance(coords, list):
        yield "Point", [coords]
    elif kind == "GeometryCollection":
        for child in geometry.get("geometries", []):
            yield from extract_geometry_parts(child)


def feature_bbox(feature: dict[str, Any]) -> tuple[float, float, float, float] | None:
    coords: list[tuple[float, float]] = []
    for _, part in extract_geometry_parts(feature.get("geometry")):
        for point in part:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                try:
                    coords.append((float(point[0]), float(point[1])))
                except (TypeError, ValueError):
                    pass
    if not coords:
        return None
    xs, ys = zip(*coords)
    return min(xs), min(ys), max(xs), max(ys)


def bbox_intersects(a: tuple[float, float, float, float],
                    b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def load_local_features(path: Path | None, bounds: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    if path is None:
        return []
    data = read_json(path)
    features = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
    selected = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        bbox = feature_bbox(feature)
        if bbox and bbox_intersects(bbox, bounds):
            selected.append(feature)
    return selected


def flatten_feature_lines(feature: dict[str, Any]) -> Iterable[tuple[list[float], list[float]]]:
    for kind, part in extract_geometry_parts(feature.get("geometry")):
        if kind == "Point":
            continue
        points = [p for p in part if isinstance(p, (list, tuple)) and len(p) >= 2]
        if len(points) >= 2:
            yield ([float(p[0]) for p in points], [float(p[1]) for p in points])


def plot_map(point_id: str, label: str, requested: tuple[float, float],
             land: tuple[float, float], nearest: tuple[float, float],
             ski_geojson: Path | None, runs_geojson: Path | None,
             out_path: Path, padding_km: float) -> dict[str, Any]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ref_lat, ref_lon = requested
    points = [(ref_lon, ref_lat), (land[1], land[0]), (nearest[1], nearest[0])]
    lat_pad = padding_km / 111.0
    lon_pad = padding_km / max(20.0, 111.0 * math.cos(math.radians(ref_lat)))
    bounds = (min(p[0] for p in points) - lon_pad,
              min(p[1] for p in points) - lat_pad,
              max(p[0] for p in points) + lon_pad,
              max(p[1] for p in points) + lat_pad)
    ski_features = load_local_features(ski_geojson, bounds)
    run_features = load_local_features(runs_geojson, bounds)

    fig, ax = plt.subplots(figsize=(10, 9))
    for feature in ski_features:
        for xs, ys in flatten_feature_lines(feature):
            ax.plot(xs, ys, linewidth=1.5, alpha=0.8, label="Ski-area boundary" if not getattr(ax, "_ski_labelled", False) else None)
            ax._ski_labelled = True
    for feature in run_features:
        for xs, ys in flatten_feature_lines(feature):
            ax.plot(xs, ys, linewidth=0.8, alpha=0.65, label="Piste/run geometry" if not getattr(ax, "_run_labelled", False) else None)
            ax._run_labelled = True

    ax.scatter([ref_lon], [ref_lat], marker="*", s=210, label="Requested weather point", zorder=5)
    ax.scatter([land[1]], [land[0]], marker="o", s=85, label="land grid-cell centre", zorder=6)
    ax.scatter([nearest[1]], [nearest[0]], marker="^", s=95, label="nearest grid-cell centre", zorder=7)
    ax.annotate("Requested", (ref_lon, ref_lat), xytext=(7, 7), textcoords="offset points")
    ax.annotate("land", (land[1], land[0]), xytext=(7, 7), textcoords="offset points")
    ax.annotate("nearest", (nearest[1], nearest[0]), xytext=(7, 7), textcoords="offset points")
    ax.set_xlim(bounds[0], bounds[2]); ax.set_ylim(bounds[1], bounds[3])
    ax.set_aspect(1 / max(0.2, math.cos(math.radians(ref_lat))))
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_title(f"{label} — requested point vs returned grid-cell centres")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)
    subtitle = "GeoJSON overlays: " + (f"{len(ski_features)} ski-area features, {len(run_features)} run features"
                                         if ski_geojson or runs_geojson else "not supplied; markers only")
    fig.text(0.5, 0.015, subtitle, ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.035, 1, 1))
    fig.savefig(out_path, dpi=160)
    plt.close(fig)
    return {"ski_area_features_drawn": len(ski_features), "run_features_drawn": len(run_features),
            "geojson_overlay_supplied": bool(ski_geojson or runs_geojson)}


def plot_timeseries(point_id: str, label: str, land_response: dict[str, Any],
                    nearest_response: dict[str, Any], out_path: Path,
                    snow_depth_review_threshold_m: float) -> dict[str, Any]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    land_hourly = land_response.get("hourly", {})
    nearest_hourly = nearest_response.get("hourly", {})
    land_times = [parse_time(t) for t in land_hourly.get("time", [])]
    nearest_times = [parse_time(t) for t in nearest_hourly.get("time", [])]
    if land_times != nearest_times:
        raise ValueError(f"Hourly timestamps differ for {point_id}; cannot plot paired series")
    n = len(land_times)
    series: dict[str, tuple[list[float | None], list[float | None]]] = {}
    for variable in VARIABLES:
        left = numeric_series(land_hourly.get(variable), n)
        right = numeric_series(nearest_hourly.get(variable), n)
        if not left or not right:
            raise ValueError(f"Missing hourly.{variable} in one response for {point_id}")
        series[variable] = left, right

    specs = [
        ("temperature_2m", "mean", "Daily mean temperature (°C)"),
        ("precipitation", "sum", "Daily precipitation (mm)"),
        ("snowfall", "sum", "Daily snowfall (cm)"),
        ("snow_depth", "mean", "Daily mean snow depth (m)"),
        ("wind_speed_10m", "mean", "Daily mean wind speed (km/h)"),
    ]
    fig, axes = plt.subplots(len(specs), 1, figsize=(13, 14), sharex=True)
    for ax, (variable, mode, title) in zip(axes, specs):
        left, right = series[variable]
        left_dates, left_daily = daily_aggregate(land_times, left, mode)
        right_dates, right_daily = daily_aggregate(nearest_times, right, mode)
        ax.plot(left_dates, left_daily, label="land", linewidth=1.0)
        ax.plot(right_dates, right_daily, label="nearest", linewidth=1.0)
        ax.set_ylabel(title)
        ax.grid(True, alpha=0.25)
        if variable == "snow_depth":
            ax.axhline(snow_depth_review_threshold_m, linestyle="--", linewidth=1.0,
                       label=f"Review threshold ({snow_depth_review_threshold_m:g} m)")
        ax.legend(loc="best", fontsize=8)
    axes[-1].set_xlabel("Date (UTC)")
    fig.suptitle(f"{label} ({point_id}) — daily aggregates, land vs nearest", y=0.995)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)

    snow_diagnostics: dict[str, Any] = {}
    for method, values in (("land", series["snow_depth"][0]), ("nearest", series["snow_depth"][1])):
        valid = [v for v in values if v is not None]
        snow_diagnostics[f"{method}_snow_depth_mean_m"] = round(sum(valid) / len(valid), 4) if valid else None
        snow_diagnostics[f"{method}_snow_depth_max_m"] = round(max(valid), 4) if valid else None
        snow_diagnostics[f"{method}_hours_above_review_threshold"] = sum(v > snow_depth_review_threshold_m for v in valid)
        snow_diagnostics[f"{method}_longest_consecutive_hours_above_review_threshold"] = consecutive_hours_above(values, snow_depth_review_threshold_m)
        jumps = [abs(b - a) for a, b in zip(values, values[1:]) if a is not None and b is not None]
        snow_diagnostics[f"{method}_max_hourly_snow_depth_change_m"] = round(max(jumps), 4) if jumps else None

    # Request and response metadata checks catch cases such as same grid centre
    # but unexpectedly different weather series.
    return {"hourly_records": n, "snow_depth_diagnostics": snow_diagnostics}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--reference-csv", type=Path, default=Path("data/reference/resorts.csv"))
    parser.add_argument("--comparison-json", type=Path, required=True,
                        help="JSON output from compare_cell_selection.py")
    parser.add_argument("--land-dir", type=Path, default=Path("data/raw/open-meteo"))
    parser.add_argument("--nearest-dir", type=Path, default=Path("data/raw/open-meteo-nearest"))
    parser.add_argument("--ski-areas-geojson", type=Path,
                        help="Optional local ski-area boundary GeoJSON")
    parser.add_argument("--runs-geojson", type=Path,
                        help="Optional local downhill-run/piste GeoJSON")
    parser.add_argument("--weather-point-id", action="append", dest="point_ids",
                        help="Repeat to select points; defaults to points in comparison JSON")
    parser.add_argument("--output-dir", type=Path, default=Path("data/interim/cell_selection_diagnostics/2023-2024"))
    parser.add_argument("--map-padding-km", type=float, default=8.0)
    parser.add_argument("--snow-depth-review-threshold-m", type=float, default=8.0,
                        help="Diagnostic flag only; does not classify data as invalid")
    args = parser.parse_args()

    root = args.project_root.resolve()
    def rooted(path: Path | None) -> Path | None:
        if path is None:
            return None
        return path if path.is_absolute() else root / path

    reference_path = rooted(args.reference_csv)
    comparison_path = rooted(args.comparison_json)
    land_dir = rooted(args.land_dir)
    nearest_dir = rooted(args.nearest_dir)
    ski_geo = rooted(args.ski_areas_geojson)
    runs_geo = rooted(args.runs_geojson)
    output_dir = rooted(args.output_dir)
    assert reference_path and comparison_path and land_dir and nearest_dir and output_dir
    if not comparison_path.exists():
        parser.error(f"Comparison JSON not found: {comparison_path}")
    if not reference_path.exists():
        parser.error(f"Reference CSV not found: {reference_path}")

    comparison = read_json(comparison_path)
    comparison_rows = {r["weather_point_id"]: r for r in comparison.get("results", [])}
    point_ids = args.point_ids or list(comparison_rows)
    reference = load_reference(reference_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries: list[dict[str, Any]] = []
    for point_id in point_ids:
        if point_id not in reference:
            print(f"WARNING: {point_id} is not in reference CSV; skipping", file=sys.stderr)
            continue
        report_row = comparison_rows.get(point_id, {})
        ref = reference[point_id]
        land_path = resolve_report_path(report_row.get("land_file", ""), root, land_dir,
                                        point_id, comparison.get("season", "2023-2024"))
        nearest_path = resolve_report_path(report_row.get("nearest_file", ""), root, nearest_dir,
                                           point_id, comparison.get("season", "2023-2024"))
        _, land_params, land_response = get_wrapped_response(land_path)
        _, nearest_params, nearest_response = get_wrapped_response(nearest_path)
        land_hourly = land_response.get("hourly", {})
        nearest_hourly = nearest_response.get("hourly", {})
        if land_hourly.get("time") != nearest_hourly.get("time"):
            raise ValueError(f"Timestamp arrays differ for {point_id}")

        land_point = (float(land_response["latitude"]), float(land_response["longitude"]))
        nearest_point = (float(nearest_response["latitude"]), float(nearest_response["longitude"]))
        requested = (float(ref["_lat"]), float(ref["_lon"]))
        land_distance = haversine_km(*requested, *land_point)
        nearest_distance = haversine_km(*requested, *nearest_point)
        label = f"{ref.get('name', point_id)} — {ref.get('ski_area_name', '')}"
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", point_id).strip("-")

        map_info = plot_map(point_id, label, requested, land_point, nearest_point,
                            ski_geo, runs_geo, output_dir / f"{slug}_map.png", args.map_padding_km)
        ts_info = plot_timeseries(point_id, label, land_response, nearest_response,
                                  output_dir / f"{slug}_timeseries.png",
                                  args.snow_depth_review_threshold_m)
        param_diff = request_differences(land_params, nearest_params)
        series_diff = hourly_difference_summary(land_hourly, nearest_hourly)
        land_meta = response_metadata(land_response)
        nearest_meta = response_metadata(nearest_response)
        metadata_diff = {key: {"land": land_meta.get(key), "nearest": nearest_meta.get(key)}
                         for key in sorted(set(land_meta) | set(nearest_meta))
                         if land_meta.get(key) != nearest_meta.get(key)}
        row = {
            "weather_point_id": point_id,
            "destination": ref.get("name", ""),
            "ski_area": ref.get("ski_area_name", ""),
            "requested_latitude": requested[0], "requested_longitude": requested[1],
            "land_grid_latitude": land_point[0], "land_grid_longitude": land_point[1],
            "land_grid_distance_km": round(land_distance, 3),
            "nearest_grid_latitude": nearest_point[0], "nearest_grid_longitude": nearest_point[1],
            "nearest_grid_distance_km": round(nearest_distance, 3),
            "distance_improvement_km": round(land_distance - nearest_distance, 3),
            "land_dem_elevation_at_requested_point_m": land_response.get("elevation"),
            "nearest_dem_elevation_at_requested_point_m": nearest_response.get("elevation"),
            "request_parameter_differences_except_cell_selection": json.dumps(param_diff, ensure_ascii=False, sort_keys=True),
            "response_metadata_differences": json.dumps(metadata_diff, ensure_ascii=False, sort_keys=True),
            "hourly_series_differences": json.dumps(series_diff, ensure_ascii=False, sort_keys=True),
            "map_geojson_features": map_info,
            "timeseries_file": f"{slug}_timeseries.png",
            "map_file": f"{slug}_map.png",
            **ts_info["snow_depth_diagnostics"],
        }
        flags = []
        if param_diff:
            flags.append("REQUEST_PARAMETERS_DIFFER");
        if metadata_diff:
            flags.append("RESPONSE_METADATA_DIFFER")
        for method in ("land", "nearest"):
            if (row.get(f"{method}_snow_depth_max_m") or 0) > args.snow_depth_review_threshold_m:
                flags.append(f"{method.upper()}_SNOW_DEPTH_ABOVE_REVIEW_THRESHOLD")
        if land_point == nearest_point:
            if param_diff or metadata_diff:
                flags.append("SAME_GRID_CENTRE_BUT_METADATA_DIFFERS")
            if any(not item["identical"] for item in series_diff.values() if item["paired_hours"]):
                flags.append("SAME_GRID_CENTRE_BUT_WEATHER_DIFFERS")
        row["review_flags"] = ";".join(dict.fromkeys(flags))
        summaries.append(row)
        print(f"{point_id}: map + time series written; review_flags={row['review_flags'] or 'none'}")

    if not summaries:
        parser.error("No points were diagnosed")

    csv_path = output_dir / "diagnostics_summary.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    report = {
        "season": comparison.get("season"),
        "comparison_json": str(comparison_path),
        "reference_csv": str(reference_path),
        "geojson_overlays": {"ski_areas": str(ski_geo) if ski_geo else None,
                              "runs": str(runs_geo) if runs_geo else None},
        "diagnostic_thresholds": {"snow_depth_review_threshold_m": args.snow_depth_review_threshold_m},
        "notes": [
            "Grid-cell centre coordinates are not the grid-cell footprint.",
            "Open-Meteo response elevation is DEM elevation at the requested coordinate, not necessarily the grid-cell-centre elevation.",
            "GeoJSON features are selected spatially by bounding-box intersection; their properties are not used to assert resort membership.",
            "Review thresholds are diagnostic prompts, not automatic data-quality verdicts.",
            "Request parameter differences exclude cell_selection; any remaining differences deserve investigation.",
        ],
        "results": summaries,
    }
    json_path = output_dir / "diagnostics_summary.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote: {csv_path}\nWrote: {json_path}\nPlots: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
