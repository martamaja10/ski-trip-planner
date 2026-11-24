"""Build the processed hourly weather dataset (Parquet) from cached Open-Meteo JSON.

Steps
-----
1. Read every cached JSON response under --raw-dir.
2. Check that each request used UTC timestamps.
3. Build one tidy hourly table (one row per weather point and hour).
4. Validate the schema.
5. Flag implausible snow-depth values (flagged, never altered or removed).
6. Write Parquet, reload it and validate it again against the raw JSON.

Raw files are never modified.

Usage (from the repository root):
    python src/ski_planner/build_dataset.py
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

LOG = logging.getLogger("build_dataset")

WEATHER_COLUMNS = [
    "temperature_2m",
    "precipitation",
    "snowfall",
    "snow_depth",
    "wind_speed_10m",
]
METADATA_COLUMNS = [
    "destination",
    "destination_id",
    "ski_area",
    "season",
    "season_start_year",
    "weather_point_id",
    "source_file",
]
EXPECTED_COLUMNS = ["timestamp", *WEATHER_COLUMNS, *METADATA_COLUMNS]

# Screening heuristic, not a scientifically validated limit.
SNOW_DEPTH_THRESHOLD_M = 10.0


def find_key(obj, key):
    """Return the first value stored under `key` anywhere in a nested dict."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = find_key(value, key)
            if found is not None:
                return found
    return None


def check_request_timezone(record: dict, path: Path) -> None:
    """Fail if the request metadata shows a non-UTC timezone."""
    timezone = find_key(record.get("request", {}), "timezone")
    if timezone is None:
        LOG.warning("%s: no timezone in request metadata; assuming UTC", path.name)
    elif str(timezone).upper() not in {"UTC", "GMT"}:
        raise ValueError(f"{path.name}: request timezone is {timezone!r}, expected UTC")


def load_one_file(path: Path, raw_dir: Path) -> pd.DataFrame:
    with open(path, "r") as f:
        record = json.load(f)

    check_request_timezone(record, path)
    hourly = record["response"]["hourly"]

    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(hourly["time"], utc=True),
            **{column: hourly[column] for column in WEATHER_COLUMNS},
        }
    )
    for column in METADATA_COLUMNS:
        if column != "source_file":
            df[column] = record[column]
    df["source_file"] = path.relative_to(raw_dir).as_posix()
    return df[EXPECTED_COLUMNS]


def build_hourly_dataset(raw_dir: Path) -> pd.DataFrame:
    files = sorted(raw_dir.rglob("*.json"))
    if not files:
        raise FileNotFoundError(f"No JSON files found under {raw_dir}")
    LOG.info("Reading %d raw files", len(files))
    return pd.concat([load_one_file(p, raw_dir) for p in files], ignore_index=True)


def validate_schema(df: pd.DataFrame) -> None:
    assert df.columns.tolist() == EXPECTED_COLUMNS, "unexpected columns"
    assert str(df["timestamp"].dt.tz) == "UTC", "timestamps must be UTC"
    for column in WEATHER_COLUMNS:
        assert pd.api.types.is_numeric_dtype(df[column]), f"{column} is not numeric"
    assert df[WEATHER_COLUMNS].notna().all().all(), "missing weather values"
    assert not df.duplicated(["weather_point_id", "season", "timestamp"]).any(), (
        "duplicate (weather point, season, timestamp) rows"
    )


def flag_snow_depth_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Add a boolean flag; values themselves are left unchanged."""
    out = df.copy()
    out["snow_depth_anomaly"] = out["snow_depth"] > SNOW_DEPTH_THRESHOLD_M
    return out


def validate_against_source(df: pd.DataFrame, raw_dir: Path) -> None:
    """Re-read the raw JSON independently and compare with the dataset."""
    files = sorted(raw_dir.rglob("*.json"))
    by_source = {name: group for name, group in df.groupby("source_file")}
    assert set(by_source) == {p.relative_to(raw_dir).as_posix() for p in files}, (
        "source files in dataset do not match raw files"
    )

    total = 0
    for path in files:
        with open(path, "r") as f:
            hourly = json.load(f)["response"]["hourly"]
        actual = by_source[path.relative_to(raw_dir).as_posix()].sort_values("timestamp")
        expected_times = pd.to_datetime(hourly["time"], utc=True)

        assert len(actual) == len(expected_times), f"row count mismatch: {path.name}"
        assert actual["timestamp"].reset_index(drop=True).equals(
            pd.Series(expected_times, name="timestamp")
        ), f"timestamp mismatch: {path.name}"
        for column in WEATHER_COLUMNS:
            assert np.array_equal(
                actual[column].to_numpy(), np.array(hourly[column])
            ), f"value mismatch: {path.name}, {column}"
        total += len(actual)

    assert len(df) == total, "total row count mismatch"
    LOG.info("Validated %d files and %s observations against raw JSON", len(files), f"{total:,}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/open-meteo"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    df = build_hourly_dataset(args.raw_dir)
    validate_schema(df)
    df = flag_snow_depth_anomalies(df)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    hourly_path = args.out_dir / "weather_hourly.parquet"
    flagged_path = args.out_dir / "flagged_snow_depth_anomalies.parquet"
    df.to_parquet(hourly_path, index=False, compression="snappy")
    df[df["snow_depth_anomaly"]].to_parquet(flagged_path, index=False, compression="snappy")

    # Validate what was actually written, not the in-memory copy.
    reloaded = pd.read_parquet(hourly_path)
    assert reloaded.equals(df), "Parquet round trip changed the data"
    validate_against_source(reloaded.drop(columns="snow_depth_anomaly"), args.raw_dir)

    LOG.info(
        "Wrote %s (%s rows, %d flagged snow-depth observations)",
        hourly_path,
        f"{len(reloaded):,}",
        int(reloaded["snow_depth_anomaly"].sum()),
    )


if __name__ == "__main__":
    main()
