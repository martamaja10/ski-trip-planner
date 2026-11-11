"""Fetch hourly historical weather for each ski-area weather point.

Run from the repository root, for example:
    python src/ski_planner/ingest.py --resorts data/reference/resorts.csv --season 2023
    python src/ski_planner/ingest.py --resorts data/reference/resorts.csv --season 2023-2024
    python src/ski_planner/ingest.py --resorts data/reference/resorts.csv --destination-id 7 --season 2023
    python src/ski_planner/ingest.py --resorts data/reference/resorts.csv --weather-point-id 2-4-vallees --season 2023

A season labelled 2023-2024 covers 1 Nov 2023 through 30 Apr 2024.
Each ski-area weather point gets its own folder and season-labelled JSON file.
Matching cached files are reused; stale or incomplete files are refreshed.
Use --force to refresh a matching file. The main archive always uses cell_selection=land.
"""
import argparse
import csv
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

URL = "https://archive-api.open-meteo.com/v1/archive"
CELL_SELECTION = "land"  # fixed baseline; nearest is retained only as a separate experiment
HOURLY = [
    "temperature_2m",
    "precipitation",
    "snowfall",
    "snow_depth",
    "wind_speed_10m",
]


def slug(value):
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def parse_season(value):
    """Return (start_year, label), accepting 2023 or 2023-2024."""
    value = str(value).strip()
    match = re.fullmatch(r"(\d{4})(?:-(\d{4}))?", value)
    if not match:
        raise argparse.ArgumentTypeError(
            "season must be a start year (e.g. 2023) or range (e.g. 2023-2024)"
        )
    start_year = int(match.group(1))
    expected_end = start_year + 1
    if match.group(2) and int(match.group(2)) != expected_end:
        raise argparse.ArgumentTypeError(
            f"season {value} must end in {expected_end}"
        )
    return start_year, f"{start_year}-{expected_end}"


def season_dates(season):
    start_year, _ = season if isinstance(season, tuple) else parse_season(season)
    return f"{start_year}-11-01", f"{start_year + 1}-04-30"


def fetch(params):
    query = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{URL}?{query}", timeout=60) as response:
        return json.load(response)


def validate_rows(rows):
    required = {
        "weather_point_id", "destination_id", "name", "ski_area_name",
        "lat", "lon",
    }
    if not rows:
        raise SystemExit("The resorts CSV contains no rows.")
    missing = required - set(rows[0])
    if missing:
        raise SystemExit(f"resorts CSV is missing required columns: {sorted(missing)}")

    seen = set()
    for row in rows:
        point_id = row["weather_point_id"].strip()
        if not point_id:
            raise SystemExit("Found a blank weather_point_id.")
        if point_id in seen:
            raise SystemExit(f"Duplicate weather_point_id: {point_id}")
        seen.add(point_id)
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (TypeError, ValueError):
            raise SystemExit(f"Invalid coordinates for {point_id}: {row['lat']}, {row['lon']}")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise SystemExit(f"Coordinates out of range for {point_id}: {lat}, {lon}")


def raw_path(root, row, season_label):
    folder_name = f"{row['weather_point_id']}_{slug(row['ski_area_name'])}"
    return Path(root) / "raw" / "open-meteo" / folder_name / f"{season_label}.json"


def cached_file_matches_request(path, row, season):
    """Return True only when an existing file belongs to this exact request.

    A matching filename is not enough: coordinates and season parameters may
    have changed since the file was first created.
    """
    start_year, season_label = season
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False

    if record.get("weather_point_id") != row["weather_point_id"]:
        return False
    if record.get("season") != season_label:
        return False

    request = record.get("request")
    params = request.get("params") if isinstance(request, dict) else None
    response = record.get("response")
    if not isinstance(params, dict) or not isinstance(response, dict):
        return False

    expected_start, expected_end = season_dates(season)
    try:
        same_coordinates = (
            abs(float(params.get("latitude")) - float(row["lat"])) <= 1e-8
            and abs(float(params.get("longitude")) - float(row["lon"])) <= 1e-8
        )
    except (TypeError, ValueError):
        return False

    # Older pilot files omitted cell_selection because the API default was land.
    # Treat that omission as land, but never reuse nearest data in the baseline.
    if params.get("cell_selection", "land") != CELL_SELECTION:
        return False

    expected_variables = set(HOURLY)
    saved_variables = set(str(params.get("hourly", "")).split(","))
    hourly = response.get("hourly")
    has_hourly_payload = isinstance(hourly, dict) and isinstance(hourly.get("time"), list)
    has_required_arrays = has_hourly_payload and all(isinstance(hourly.get(name), list) for name in HOURLY)
    if not has_required_arrays:
        return False

    start_dt = datetime.fromisoformat(expected_start)
    end_dt = datetime.fromisoformat(expected_end) + timedelta(hours=23)
    expected_count = int((end_dt - start_dt).total_seconds() // 3600) + 1
    if len(hourly["time"]) != expected_count:
        return False
    if any(len(hourly[name]) != expected_count for name in HOURLY):
        return False
    try:
        saved_times = [datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
                       for value in hourly["time"]]
    except (AttributeError, TypeError, ValueError):
        return False
    expected_times = [start_dt + timedelta(hours=i) for i in range(expected_count)]

    return (
        same_coordinates
        and params.get("start_date") == expected_start
        and params.get("end_date") == expected_end
        and params.get("timezone") == "UTC"
        and expected_variables.issubset(saved_variables)
        and saved_times == expected_times
    )


def ingest_one(row, season, root="data", force=False):
    start_year, season_label = season
    path = raw_path(root, row, season_label)
    label = f"{row['name']} / {row['ski_area_name']} ({season_label} season)"
    if path.exists() and not force:
        if cached_file_matches_request(path, row, season):
            print(f"skip   {label} (matching request already cached: {path.name})")
            return path
        print(f"refresh {label} (cached request does not match current reference/season)")

    start, end = season_dates(season)
    params = {
        "latitude": float(row["lat"]),
        "longitude": float(row["lon"]),
        "start_date": start,
        "end_date": end,
        "hourly": ",".join(HOURLY),
        "timezone": "UTC",
        "cell_selection": CELL_SELECTION,
    }
    response = fetch(params)
    record = {
        "season": season_label,
        "season_start_year": start_year,
        "destination": row["name"],
        "destination_id": row["destination_id"],
        "ski_area": row["ski_area_name"],
        "weather_point_id": row["weather_point_id"],
        "weather_point": {
            "latitude": float(row["lat"]),
            "longitude": float(row["lon"]),
            "selected_point_elevation_m": (
                float(row["weather_point_alt_m"])
                if row.get("weather_point_alt_m", "").strip() else None
            ),
            "coordinate_source": row.get("coordinate_source", ""),
            "coordinate_status": row.get("coordinate_status", ""),
        },
        "request": {"url": URL, "params": params},
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "response": response,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    print(
        f"saved  {label} -> {path} "
        f"(Open-Meteo DEM elevation at requested point: {response.get('elevation')} m)"
    )
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resorts", required=True, help="single reference CSV, one row per ski-area weather point")
    parser.add_argument("--season", required=True, help="start year (2023) or season label (2023-2024)")
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--destination-id", "--resort-id", dest="destination_id",
                          help="process all ski areas belonging to one destination (legacy --resort-id alias)")
    selector.add_argument("--weather-point-id", help="process exactly one ski-area weather point")
    parser.add_argument("--root", default="data")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    season = parse_season(args.season)
    with open(args.resorts, encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    validate_rows(rows)

    if args.destination_id:
        rows = [r for r in rows if r["destination_id"].strip() == str(args.destination_id)]
        if not rows:
            raise SystemExit(f"no destination with destination_id {args.destination_id}")
    elif args.weather_point_id:
        rows = [r for r in rows if r["weather_point_id"].strip() == args.weather_point_id]
        if not rows:
            raise SystemExit(f"no weather point with weather_point_id {args.weather_point_id}")

    for index, row in enumerate(rows):
        ingest_one(row, season, args.root, args.force)
        if index < len(rows) - 1:
            time.sleep(1)  # be gentle with the free API


if __name__ == "__main__":
    main()
