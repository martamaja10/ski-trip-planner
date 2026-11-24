import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "ski_planner"))
import build_dataset as bd  # noqa: E402


def write_fake_response(raw_dir: Path, name: str, snow_depth: float, timezone: str = "UTC"):
    times = [f"2024-01-01T{h:02d}:00" for h in range(24)]
    record = {
        "destination": "Testville",
        "destination_id": "testville",
        "ski_area": "Testville Ski Area",
        "season": "2023-2024",
        "season_start_year": 2023,
        "weather_point_id": name,
        "request": {"params": {"timezone": timezone}},
        "response": {
            "hourly": {
                "time": times,
                "temperature_2m": [-5.0] * 24,
                "precipitation": [0.0] * 24,
                "snowfall": [0.0] * 24,
                "snow_depth": [snow_depth] * 24,
                "wind_speed_10m": [10.0] * 24,
            }
        },
    }
    (raw_dir / f"{name}.json").write_text(json.dumps(record))


class BuildDatasetTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.raw_dir = Path(self._tmp.name)
        write_fake_response(self.raw_dir, "wp1", 1.5)
        write_fake_response(self.raw_dir, "wp2", 33.33)

    def tearDown(self):
        self._tmp.cleanup()

    def test_row_count_and_schema(self):
        df = bd.build_hourly_dataset(self.raw_dir)
        bd.validate_schema(df)
        self.assertEqual(len(df), 48)
        self.assertEqual(df["source_file"].nunique(), 2)

    def test_validation_against_source_passes(self):
        df = bd.build_hourly_dataset(self.raw_dir)
        bd.validate_against_source(df, self.raw_dir)

    def test_validation_detects_changed_value(self):
        df = bd.build_hourly_dataset(self.raw_dir)
        df.loc[0, "temperature_2m"] += 1.0
        with self.assertRaises(AssertionError):
            bd.validate_against_source(df, self.raw_dir)

    def test_anomaly_flag_keeps_values_unchanged(self):
        df = bd.build_hourly_dataset(self.raw_dir)
        flagged = bd.flag_snow_depth_anomalies(df)
        self.assertTrue(flagged.loc[flagged["weather_point_id"] == "wp2", "snow_depth_anomaly"].all())
        self.assertFalse(flagged.loc[flagged["weather_point_id"] == "wp1", "snow_depth_anomaly"].any())
        pd.testing.assert_series_equal(flagged["snow_depth"], df["snow_depth"])

    def test_non_utc_request_is_rejected(self):
        write_fake_response(self.raw_dir, "wp3", 1.0, timezone="Europe/Zurich")
        with self.assertRaises(ValueError):
            bd.build_hourly_dataset(self.raw_dir)


if __name__ == "__main__":
    unittest.main()
