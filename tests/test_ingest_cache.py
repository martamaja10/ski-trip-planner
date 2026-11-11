import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ski_planner.ingest import cached_file_matches_request


class IngestCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "2023-2024.json"
        self.row = {
            "weather_point_id": "test-point", "lat": "46.1", "lon": "7.2",
            "ski_area_name": "Test Area",
        }
        self.season = (2023, "2023-2024")
        self.record = {
            "season": "2023-2024", "weather_point_id": "test-point",
            "request": {"params": {
                "latitude": 46.1, "longitude": 7.2,
                "start_date": "2023-11-01", "end_date": "2024-04-30",
                "hourly": "temperature_2m,precipitation,snowfall,snow_depth,wind_speed_10m",
                "timezone": "UTC",
            }},
            "response": {"hourly": {
                "time": [(datetime(2023, 11, 1) + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
                         for i in range(4368)],
                "temperature_2m": [0] * 4368, "precipitation": [0] * 4368,
                "snowfall": [0] * 4368, "snow_depth": [0] * 4368,
                "wind_speed_10m": [0] * 4368,
            }},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def write(self):
        self.path.write_text(json.dumps(self.record), encoding="utf-8")

    def test_matching_legacy_land_request_is_reused(self):
        # The original pilot omitted cell_selection; Open-Meteo's default was land.
        self.write()
        self.assertTrue(cached_file_matches_request(self.path, self.row, self.season))


    def test_changed_coordinates_are_not_reused(self):
        self.record["request"]["params"]["latitude"] = 46.2
        self.write()
        self.assertFalse(cached_file_matches_request(self.path, self.row, self.season))

    def test_missing_point_metadata_is_not_reused(self):
        del self.record["weather_point_id"]
        self.write()
        self.assertFalse(cached_file_matches_request(self.path, self.row, self.season))

    def test_missing_hourly_array_is_not_reused(self):
        del self.record["response"]["hourly"]["snow_depth"]
        self.write()
        self.assertFalse(cached_file_matches_request(self.path, self.row, self.season))

    def test_incomplete_season_is_not_reused(self):
        self.record["response"]["hourly"]["time"].pop()
        self.write()
        self.assertFalse(cached_file_matches_request(self.path, self.row, self.season))
        
    def test_nearest_cache_is_not_reused(self):
        self.record["request"]["params"]["cell_selection"] = "nearest"
        self.write()
        self.assertFalse(cached_file_matches_request(self.path, self.row, self.season))


if __name__ == "__main__":
    unittest.main()
