#!/usr/bin/env python3
"""Generate piste-based weather-point candidates from OpenSkiData.

Matching is deliberately conservative: a ski-area feature is selected only
when its normalized name exactly matches the reference ski_area_name or one of
the explicitly curated aliases below, in the expected country. Fuzzy matches
are printed in the audit as suggestions only; they are never selected.

Only downhill runs with an explicit association to the selected ski-area
feature are eligible for candidate generation. Nearby/spatially intersecting
runs are counted for diagnostics only and are never used in the calculation.

The script reports run-count elevation coverage and creates inspection maps.
It never edits the reference CSV. Review candidates against official piste maps
and check the Open-Meteo grid cell before adoption.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from pyproj import Geod, Transformer
from rapidfuzz import fuzz
from shapely.geometry import Point, shape
from shapely.ops import transform

BASE_URL = "https://tiles.openskimap.org/geojson"
DATASETS = {
    "ski_areas": f"{BASE_URL}/ski_areas.geojson",
    "runs": f"{BASE_URL}/runs.geojson",
}
GEOD = Geod(ellps="WGS84")

# Only add aliases when the intended OpenSkiData feature name is known.
# Do not use broad place names (e.g. "Argentière") as ski-area aliases.
ALIASES: dict[str, list[str]] = {
    "2-4-vallees": [
        "4 Vallées – Verbier/La Tzoumaz/Nendaz/Veysonnaz/Thyon",
    ],
    "7-kleine-scheidegg-maennlichen": [
        "Grindelwald - Wengen (Kleine Scheidegg - Männlichen)",
    ],
    "13-brevent-flegere": [
        "Brévent/Flégère (Chamonix)",
    ],
    "17-pila": [
        "Pila ski",
    ],
    "18-la-thuile": [
        "La Thuile ski",
    ],
}


def norm(value: Any) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def read_csv(path: Path) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "cp1252", "latin1"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Could not decode CSV: {path}")


def validate_reference_csv(resorts: pd.DataFrame) -> pd.DataFrame:
    """Validate the project's one-row-per-weather-point reference table."""
    required = {"weather_point_id", "destination_id", "name", "ski_area_name", "country", "lat", "lon"}
    missing = required - set(resorts.columns)
    if missing:
        raise SystemExit(f"resorts CSV missing columns: {sorted(missing)}")
    if resorts.empty:
        raise SystemExit("resorts CSV contains no weather points")

    resorts = resorts.copy()
    for column in ("weather_point_id", "name", "ski_area_name", "country"):
        resorts[column] = resorts[column].astype(str).str.strip()
        if resorts[column].eq("").any() or resorts[column].str.lower().eq("nan").any():
            raise SystemExit(f"resorts CSV contains blank values in {column}")

    if resorts["weather_point_id"].duplicated().any():
        duplicates = sorted(resorts.loc[resorts["weather_point_id"].duplicated(False), "weather_point_id"].unique())
        raise SystemExit(f"Duplicate weather_point_id values: {duplicates}")

    try:
        destination_ids = pd.to_numeric(resorts["destination_id"], errors="raise")
        coordinates = resorts[["lat", "lon"]].apply(pd.to_numeric, errors="raise")
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Invalid numeric destination ID or coordinates: {exc}")

    if not (destination_ids.notna().all() and (destination_ids % 1 == 0).all()):
        raise SystemExit("destination_id values must be whole numbers")
    destination_ids = destination_ids.astype(int)
    expected_ids = set(range(1, int(destination_ids.max()) + 1))
    actual_ids = set(destination_ids.unique())
    if actual_ids != expected_ids:
        raise SystemExit(
            f"destination_id values must be consecutive from 1; found {sorted(actual_ids)}"
        )

    if not coordinates.apply(lambda col: col.map(math.isfinite)).all().all():
        raise SystemExit("lat/lon must contain finite numeric values")
    if not coordinates["lat"].between(-90, 90).all() or not coordinates["lon"].between(-180, 180).all():
        raise SystemExit("lat/lon contains coordinates outside valid geographic ranges")

    for point_id, destination_id in zip(resorts["weather_point_id"], destination_ids):
        prefix = point_id.split("-", 1)[0]
        if not prefix.isdigit() or int(prefix) != int(destination_id):
            raise SystemExit(
                f"weather_point_id {point_id!r} does not start with destination_id {destination_id}"
            )

    if resorts[["lat", "lon"]].apply(pd.to_numeric).duplicated().any():
        raise SystemExit("Duplicate weather-point coordinates found; review before validation")

    text_columns = [column for column in resorts.columns if resorts[column].dtype == object]
    if any(resorts[column].str.contains("andermatt", case=False, na=False).any() for column in text_columns):
        raise SystemExit("Andermatt is still present in resorts.csv; remove it before validation")

    return resorts


def download_dataset(name: str, url: str, cache_dir: Path, refresh: bool) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{name}.geojson"
    if path.exists() and not refresh:
        print(f"Using cached {name}: {path}")
        return path

    print(f"Downloading {name} from {url}")
    response = requests.get(
        url,
        headers={"User-Agent": "ski-trip-planner-coordinate-validation/0.2"},
        timeout=(30, 300),
        stream=True,
    )
    response.raise_for_status()
    temp = path.with_suffix(".download")
    decompressor = None
    with temp.open("wb") as output:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            if decompressor is None and chunk[:2] == b"\x1f\x8b":
                import zlib
                decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
                output.write(decompressor.decompress(chunk))
            elif decompressor is not None:
                output.write(decompressor.decompress(chunk))
            else:
                output.write(chunk)
        if decompressor is not None:
            output.write(decompressor.flush())
    temp.replace(path)
    with path.open(encoding="utf-8") as file:
        json.load(file)
    print(f"Saved {name}: {path} ({path.stat().st_size / 1e6:.1f} MB)")
    return path


def load_features(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as file:
        data = json.load(file)
    features = data.get("features")
    if not isinstance(features, list):
        raise RuntimeError(f"{path} is not a GeoJSON FeatureCollection")
    return features


def get_name(props: dict) -> str:
    for key in ("name", "name_en", "name:en", "title"):
        if props.get(key):
            return str(props[key])
    return ""


def get_country(props: dict) -> str:
    for place in props.get("places", []) or []:
        if isinstance(place, dict) and place.get("iso3166_1Alpha2"):
            return str(place["iso3166_1Alpha2"]).upper()
    for key in ("country", "country_code", "iso_country"):
        if props.get(key):
            return str(props[key]).upper()
    return ""


def match_ski_area(features: list[dict], query: str, aliases: list[str], country: str):
    accepted_names = {norm(query), *(norm(alias) for alias in aliases)}
    exact = []
    suggestions = []
    for feature in features:
        props = feature.get("properties") or {}
        name = get_name(props)
        geometry = feature.get("geometry")
        if not name or not geometry:
            continue
        feature_country = get_country(props)
        if feature_country != country:
            continue
        status = str(props.get("status", "")).lower()
        score = fuzz.token_set_ratio(norm(query), norm(name))
        suggestions.append((score, name, feature_country, status, feature))
        if norm(name) in accepted_names:
            exact.append((name, feature_country, status, feature))

    suggestions.sort(key=lambda item: item[0], reverse=True)
    # If the exact name occurs more than once, prefer a single operating
    # feature. If still ambiguous, stop rather than choosing arbitrarily.
    operating = [item for item in exact if item[2] == "operating"]
    eligible = operating if operating else [item for item in exact if item[2] != "abandoned"]
    if len(eligible) == 1:
        name, feature_country, status, feature = eligible[0]
        return {
            "feature": feature,
            "name": name,
            "country": feature_country,
            "status": status,
            "match_type": "exact_normalized_name_or_curated_alias",
            "suggestions": suggestions[:5],
        }
    if len(eligible) > 1:
        names = [(item[0], item[2], item[3]) for item in eligible]
        raise ValueError(f"ambiguous_exact_match: {names}")

    suggestions.sort(key=lambda item: item[0], reverse=True)
    suggestion_text = [
        {"score": round(score, 1), "name": name, "country": country_code, "status": status}
        for score, name, country_code, status, _ in suggestions[:5]
    ]
    if exact:
        names = [(item[0], item[2], item[3]) for item in exact]
        raise ValueError(f"no_operating_exact_match: exact_candidates={names}; fuzzy_suggestions={suggestion_text}")
    raise ValueError(f"no_exact_match: fuzzy_suggestions={suggestion_text}")


def area_match_keys(feature: dict) -> set[str]:
    props = feature.get("properties") or {}
    keys: set[str] = set()
    if feature.get("id") is not None:
        keys.add(str(feature["id"]))
    if props.get("id") is not None:
        keys.add(str(props["id"]))
    for source in props.get("sources", []) or []:
        if isinstance(source, dict) and source.get("id") is not None:
            keys.add(str(source["id"]))
    return keys


def recursively_contains(value: Any, target_keys: set[str]) -> bool:
    """Find an area identifier only inside likely association properties."""
    association_tokens = ("ski_area", "skiarea", "site", "area")

    def contains_scalar(item: Any) -> bool:
        if isinstance(item, (str, int)):
            return str(item) in target_keys
        if isinstance(item, list):
            return any(contains_scalar(child) for child in item)
        if isinstance(item, dict):
            return any(contains_scalar(child) for child in item.values())
        return False

    if isinstance(value, dict):
        for key, child in value.items():
            key_lower = str(key).lower()
            if any(token in key_lower for token in association_tokens):
                if contains_scalar(child):
                    return True
                if isinstance(child, dict):
                    for id_key in ("id", "feature_id", "source_id"):
                        if child.get(id_key) is not None and str(child[id_key]) in target_keys:
                            return True
            if recursively_contains(child, target_keys):
                return True
    elif isinstance(value, list):
        return any(recursively_contains(item, target_keys) for item in value)
    return False


def make_area_geometry(feature: dict):
    try:
        geometry = shape(feature["geometry"])
        return None if geometry.is_empty else geometry
    except Exception:
        return None


def iter_lines(geometry: dict):
    if not geometry:
        return
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "LineString":
        yield coords
    elif kind == "MultiLineString":
        yield from coords


def run_metrics(feature: dict):
    """Return 3D midpoint samples for one run, weighted by segment length."""
    samples = []
    for line in iter_lines(feature.get("geometry") or {}):
        if not isinstance(line, list) or len(line) < 2:
            continue
        for a, b in zip(line[:-1], line[1:]):
            try:
                lon1, lat1 = float(a[0]), float(a[1])
                lon2, lat2 = float(b[0]), float(b[1])
            except (TypeError, ValueError, IndexError):
                continue
            _, _, length = GEOD.inv(lon1, lat1, lon2, lat2)
            if not math.isfinite(length) or length <= 0 or len(a) < 3 or len(b) < 3:
                continue
            try:
                z1, z2 = float(a[2]), float(b[2])
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(z1) and math.isfinite(z2)):
                continue
            samples.append(((lon1 + lon2) / 2, (lat1 + lat2) / 2, (z1 + z2) / 2, length))
    return samples


def feature_is_downhill(props: dict) -> bool:
    uses = props.get("uses")
    if uses is None:
        return True
    if isinstance(uses, str):
        uses = [uses]
    if not isinstance(uses, list):
        return True
    normalized = {norm(value) for value in uses}
    return "downhill" in normalized or not normalized


def local_transformers(lon: float, lat: float):
    local_crs = f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs"
    forward = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True).transform
    inverse = Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True).transform
    return forward, inverse


def weighted_median(values: list[tuple[float, float]]) -> float:
    values = sorted(values, key=lambda item: item[0])
    total = sum(weight for _, weight in values)
    cumulative = 0.0
    for value, weight in values:
        cumulative += weight
        if cumulative >= total / 2:
            return value
    return values[-1][0]


def select_candidate(samples: list[tuple[float, float, float, float]], area_center: Point):
    median_elev = weighted_median([(sample[2], sample[3]) for sample in samples])
    forward, _ = local_transformers(area_center.x, area_center.y)
    projected = []
    total_length = 0.0
    for lon, lat, elev, length in samples:
        x, y = forward(lon, lat)
        projected.append((x, y, elev, length, lon, lat))
        total_length += length
    centre_x = sum(x * length for x, _, _, length, _, _ in projected) / total_length
    centre_y = sum(y * length for _, y, _, length, _, _ in projected) / total_length
    chosen = min(
        projected,
        key=lambda item: (
            abs(item[2] - median_elev),
            math.hypot(item[0] - centre_x, item[1] - centre_y),
        ),
    )
    return {
        "candidate_lon": chosen[4],
        "candidate_lat": chosen[5],
        "candidate_alt_m": chosen[2],
        "piste_length_weighted_median_alt_m": median_elev,
        "piste_network_length_km": total_length / 1000,
        "candidate_method": "explicitly_associated_piste_segment_midpoint_near_weighted_median_elevation",
    }


def spatial_run_count(run_features: list[dict], area_geom, buffer_km: float) -> int:
    """Diagnostic only: count nearby downhill runs; never use these for candidates."""
    if area_geom is None:
        return 0
    center = area_geom.centroid
    forward, inverse = local_transformers(center.x, center.y)
    if area_geom.geom_type == "Point":
        projected_area = transform(forward, area_geom).buffer(buffer_km * 1000)
    else:
        projected_area = transform(forward, area_geom).buffer(150)
    diagnostic_geom = transform(inverse, projected_area)
    count = 0
    for run in run_features:
        if not feature_is_downhill(run.get("properties") or {}):
            continue
        try:
            geom = shape(run.get("geometry") or {})
            if not geom.is_empty and geom.intersects(diagnostic_geom):
                count += 1
        except Exception:
            continue
    return count



def plot_validation_map(
    row,
    area_feature: dict,
    associated_runs: list[dict],
    candidate: dict,
    output_path: Path,
) -> None:
    """Plot the matched area geometry (if polygon), associated pistes and points."""
    import matplotlib.pyplot as plt

    area_geom = make_area_geometry(area_feature)
    fig, ax = plt.subplots(figsize=(10, 9))

    # Plot explicitly associated downhill pistes only.
    pistes_labelled = False
    for run in associated_runs:
        try:
            geom = shape(run.get("geometry") or {})
        except Exception:
            continue
        if geom.is_empty:
            continue
        geoms = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
        for part in geoms:
            if part.geom_type == "LineString":
                x, y = part.xy
                ax.plot(
                    x, y, color="#3977a8", linewidth=0.75, alpha=0.65, zorder=2,
                    label="Explicitly associated downhill pistes" if not pistes_labelled else None,
                )
                pistes_labelled = True

    # A polygon is a genuine source boundary; a point feature is shown only
    # as a marker and is not converted into an invented ski-area boundary.
    if area_geom is not None:
        if area_geom.geom_type in ("Polygon", "MultiPolygon"):
            polygons = list(area_geom.geoms) if area_geom.geom_type == "MultiPolygon" else [area_geom]
            for polygon in polygons:
                x, y = polygon.exterior.xy
                ax.plot(x, y, color="#333333", linewidth=1.6, label="OpenSkiData ski-area boundary", zorder=3)
                for interior in polygon.interiors:
                    ix, iy = interior.xy
                    ax.plot(ix, iy, color="#333333", linewidth=0.8, linestyle=":", zorder=3)
        elif area_geom.geom_type == "Point":
            ax.scatter(
                [area_geom.x], [area_geom.y], marker="D", s=55,
                color="#9467bd", edgecolor="white", linewidth=0.7,
                label="OpenSkiData feature point (not a boundary)", zorder=5,
            )

    current = (float(row["lon"]), float(row["lat"]))
    ax.scatter(
        [current[0]], [current[1]], marker="x", s=95, linewidth=2.2,
        color="#d62728", label="Current weather point", zorder=7,
    )
    ax.scatter(
        [candidate["candidate_lon"]], [candidate["candidate_lat"]],
        marker="*", s=190, color="#2ca02c", edgecolor="black",
        linewidth=0.6, label="Candidate weather point", zorder=8,
    )

    # Fit the view to all piste coordinates, the area feature and both points.
    xs, ys = [current[0], candidate["candidate_lon"]], [current[1], candidate["candidate_lat"]]
    if area_geom is not None:
        minx, miny, maxx, maxy = area_geom.bounds
        xs.extend([minx, maxx])
        ys.extend([miny, maxy])
    for run in associated_runs:
        try:
            geom = shape(run.get("geometry") or {})
            if not geom.is_empty:
                minx, miny, maxx, maxy = geom.bounds
                xs.extend([minx, maxx])
                ys.extend([miny, maxy])
        except Exception:
            continue
    xspan, yspan = max(xs) - min(xs), max(ys) - min(ys)
    padx = max(xspan * 0.08, 0.003)
    pady = max(yspan * 0.08, 0.003)
    ax.set_xlim(min(xs) - padx, max(xs) + padx)
    ax.set_ylim(min(ys) - pady, max(ys) + pady)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(f"{row.get('name', row['ski_area_name'])} — {row['ski_area_name']}\n"
                 f"Associated downhill pistes and weather-point candidates")
    ax.grid(True, linewidth=0.35, alpha=0.35)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def validate_one(row, area_features, run_features, min_run_count: int, buffer_km: float, map_dir: Path | None = None):
    point_id = str(row["weather_point_id"])
    query = str(row["ski_area_name"])
    country = str(row["country"]).upper()
    aliases = ALIASES.get(point_id, [])
    result = {
        "weather_point_id": point_id,
        "destination": row.get("name", ""),
        "ski_area_name_csv": query,
        "current_lat": float(row["lat"]),
        "current_lon": float(row["lon"]),
        "validation_status": "not_processed",
    }
    audit = {"weather_point_id": point_id, "query": query, "country": country, "aliases": aliases}

    try:
        match = match_ski_area(area_features, query, aliases, country)
    except ValueError as exc:
        result["validation_status"] = str(exc).split(":", 1)[0]
        audit["matching_error"] = str(exc)
        return result, audit

    feature = match["feature"]
    area_geom = make_area_geometry(feature)
    if area_geom is None:
        result["validation_status"] = "matched_area_has_no_usable_geometry"
        audit["matched_ski_area"] = match["name"]
        return result, audit

    props = feature.get("properties") or {}
    keys = area_match_keys(feature)
    associated = []
    for run in run_features:
        run_props = run.get("properties") or {}
        if feature_is_downhill(run_props) and recursively_contains(run_props, keys):
            associated.append(run)

    nearby_count = spatial_run_count(run_features, area_geom, buffer_km)
    samples = []
    runs_with_3d = 0
    for run in associated:
        run_samples = run_metrics(run)
        samples.extend(run_samples)
        if run_samples:
            runs_with_3d += 1

    audit.update({
        "matched_ski_area": match["name"],
        "matched_country": match["country"],
        "matched_status": match["status"],
        "match_type": match["match_type"],
        "matched_feature_id": feature.get("id", props.get("id")),
        "match_suggestions_diagnostic_only": [
            {"score": round(score, 1), "name": name, "country": country_code, "status": status}
            for score, name, country_code, status, _ in match["suggestions"]
        ],
        "matched_area_properties": props,
        "area_geometry_type": area_geom.geom_type,
        "area_geometry_bounds": list(area_geom.bounds),
        "association_ids": sorted(keys),
        "associated_run_count": len(associated),
        "nearby_spatial_run_count_diagnostic_only": nearby_count,
        "runs_with_3d_elevation": runs_with_3d,
        "runs_without_3d_elevation": len(associated) - runs_with_3d,
        "run_3d_coverage_pct": (100 * runs_with_3d / len(associated)) if associated else 0,
        "sampled_3d_segment_count": len(samples),
    })
    result.update({
        "matched_openskidata_name": match["name"],
        "matched_country": match["country"],
        "matched_feature_id": feature.get("id", props.get("id")),
        "match_type": match["match_type"],
        "associated_run_count": len(associated),
        "nearby_spatial_run_count_diagnostic_only": nearby_count,
        "runs_with_3d_elevation": runs_with_3d,
        "runs_without_3d_elevation": len(associated) - runs_with_3d,
        "run_3d_coverage_pct": (100 * runs_with_3d / len(associated)) if associated else 0,
        "sampled_3d_segment_count": len(samples),
    })

    if len(associated) < min_run_count:
        result["validation_status"] = "insufficient_explicitly_associated_runs"
        audit["validation_status"] = result["validation_status"]
        return result, audit
    if not samples:
        result["validation_status"] = "no_3d_elevation_segments_in_associated_runs"
        audit["validation_status"] = result["validation_status"]
        return result, audit

    candidate = select_candidate(samples, area_geom.centroid)
    _, _, distance_m = GEOD.inv(
        float(row["lon"]), float(row["lat"]), candidate["candidate_lon"], candidate["candidate_lat"]
    )
    if map_dir is not None:
        safe_id = re.sub(r"[^a-z0-9-]+", "-", point_id.lower()).strip("-")
        map_path = map_dir / f"{safe_id}.png"
        plot_validation_map(row, feature, associated, candidate, map_path)
        result["validation_map"] = str(map_path)
        audit["validation_map"] = str(map_path)
    result.update(candidate)
    result["distance_from_current_point_m"] = distance_m
    result["validation_status"] = "candidate_for_visual_review"
    audit["candidate"] = candidate
    audit["distance_from_current_point_m"] = distance_m
    audit["validation_status"] = result["validation_status"]
    return result, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resorts", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/interim/weather_point_validation"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/external/openskidata"))
    parser.add_argument("--weather-point-id", action="append", help="process one ID; repeat to process several")
    parser.add_argument("--all", action="store_true", help="process every row in resorts.csv (default if no IDs are supplied)")
    parser.add_argument("--refresh", action="store_true", help="redownload source GeoJSON")
    parser.add_argument("--buffer-km", type=float, default=8.0,
                        help="diagnostic-only radius around point-type ski-area features")
    parser.add_argument("--min-run-count", type=int, default=3)
    args = parser.parse_args()

    resorts = validate_reference_csv(read_csv(args.resorts))
    if args.all and args.weather_point_id:
        raise SystemExit("Use either --all or --weather-point-id, not both.")

    if args.all or not args.weather_point_id:
        selected = resorts.copy()
    else:
        selected = resorts[resorts["weather_point_id"].isin(args.weather_point_id)].copy()
        missing_ids = set(args.weather_point_id) - set(selected["weather_point_id"])
        if missing_ids:
            raise SystemExit(f"Unknown weather_point_id values: {sorted(missing_ids)}")

    area_path = download_dataset("ski_areas", DATASETS["ski_areas"], args.cache_dir, args.refresh)
    runs_path = download_dataset("runs", DATASETS["runs"], args.cache_dir, args.refresh)
    area_features = load_features(area_path)
    run_features = load_features(runs_path)

    # Create the map output directory before validation, because validate_one()
    # writes each map as soon as its candidate has been calculated.
    args.output_dir.mkdir(parents=True, exist_ok=True)
    map_dir = args.output_dir / "maps"
    map_dir.mkdir(parents=True, exist_ok=True)

    results, audits = [], []
    for _, row in selected.iterrows():
        print(f"\nProcessing {row['weather_point_id']}: {row['ski_area_name']}")
        result, audit = validate_one(row, area_features, run_features, args.min_run_count, args.buffer_km, map_dir)
        results.append(result)
        audits.append(audit)
        if result["validation_status"] == "candidate_for_visual_review":
            print(
                f"  matched: {result['matched_openskidata_name']} | "
                f"associated runs: {result['associated_run_count']} | "
                f"runs with 3D: {result['runs_with_3d_elevation']} "
                f"({result['run_3d_coverage_pct']:.1f}%) | "
                f"candidate: {result['candidate_lat']:.6f}, {result['candidate_lon']:.6f}, "
                f"{result['candidate_alt_m']:.0f} m"
            )
        else:
            print(f"  NOT READY: {result['validation_status']}")

    suffix = "all" if args.all or not args.weather_point_id else "selected"
    candidates_path = args.output_dir / f"weather_point_candidates_{suffix}.csv"
    audit_path = args.output_dir / f"weather_point_audit_{suffix}.json"
    pd.DataFrame(results).to_csv(candidates_path, index=False)
    audit_path.write_text(json.dumps({
        "source": "OpenSkiData / OpenSkiMap",
        "source_urls": DATASETS,
        "matching_policy": "exact normalized name or curated exact alias; country constrained; fuzzy matches diagnostic only",
        "run_selection_policy": "explicit ski-area association only; spatial intersections diagnostic only",
        "candidate_method": "length-weighted median elevation; choose an actual associated piste segment midpoint nearest elevation and length-weighted network centre",
        "results": audits,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nFinished. resorts.csv was not modified by validation.")
    print(f"Candidates: {candidates_path}")
    print(f"Audit:      {audit_path}")
    print(f"Maps:       {map_dir}")
    print("Only rows marked candidate_for_visual_review have generated coordinates.")


if __name__ == "__main__":
    main()
