import csv
import json
import tempfile
import unittest
import sys
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import check_weather_data as checker


class CheckWeatherDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.csv_path = self.root / "resorts.csv"
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "weather_point_id", "destination_id", "name", "ski_area_name", "lat", "lon",
                "ski_area_base_alt_m", "ski_area_top_alt_m",
            ])
            writer.writeheader()
            writer.writerow({
                "weather_point_id": "test-area", "destination_id": "1", "name": "Test",
                "ski_area_name": "Test Area", "lat": "46.0", "lon": "7.0",
                "ski_area_base_alt_m": "1000", "ski_area_top_alt_m": "2000",
            })
        self.reference = checker.load_reference(self.csv_path)
        self.file = self.root / "test-area_test-area" / "2023-2024.json"
        self.file.parent.mkdir()
        times = [f"2023-11-01T{i:02d}:00" for i in range(24)]
        self.record = {
            "season": "2023-2024", "weather_point_id": "test-area",
            "request": {"params": {
                "latitude": "46.0", "longitude": "7.0", "start_date": "2023-11-01",
                "end_date": "2024-04-30", "hourly": ",".join(checker.VARIABLE_LIMITS), "timezone": "UTC",
            }},
            "response": {
                "latitude": 46.02, "longitude": 7.01, "elevation": 1500,
                "hourly_units": {k: v[2] for k, v in checker.VARIABLE_LIMITS.items()},
                "hourly": {
                    "time": times,
                    "temperature_2m": [0.0] * 24,
                    "precipitation": [0.0] * 24,
                    "snowfall": [0.0] * 24,
                    "snow_depth": [0.5] * 24,
                    "wind_speed_10m": [5.0] * 24,
                },
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def write_record(self):
        self.file.write_text(json.dumps(self.record), encoding="utf-8")

    def test_current_schema_and_grid_metadata(self):
        self.write_record()
        result = checker.check_file(self.file, self.reference, 5.0)
        self.assertEqual(result["weather_point_id"], "test-area")
        self.assertEqual(result["dem_elevation_at_requested_point_m"], 1500.0)
        self.assertNotIn("grid_cell_elevation_m", result)
        self.assertEqual(result["dem_elevation_relation_to_ski_area"], "within ski-area altitude range")
        self.assertGreater(result["grid_distance_km"], 0)
        self.assertEqual(result["status"], "FAIL")  # 24 hours instead of full season
        self.assertIn("exactly cover 2023-2024", result["errors"])

    def test_complete_season_passes(self):
        expected = checker.expected_hourly_times(*checker.season_dates("2023-2024"))
        self.record["response"]["hourly"]["time"] = [t.strftime("%Y-%m-%dT%H:%M") for t in expected]
        for variable in checker.VARIABLE_LIMITS:
            value = 0.5 if variable == "snow_depth" else 0.0
            self.record["response"]["hourly"][variable] = [value] * len(expected)
        self.write_record()
        result = checker.check_file(self.file, self.reference, 5.0)
        self.assertEqual(result["hourly_records"], 4368)
        self.assertEqual(result["expected_hourly_records"], 4368)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["grid_location_review"], "WITHIN_THRESHOLD")

    def test_stale_request_coordinates_fail(self):
        self.record["request"]["params"]["latitude"] = "46.2"
        self.write_record()
        result = checker.check_file(self.file, self.reference, 5.0)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("stale cache file", result["errors"])

    def test_out_of_range_snow_depth_fails(self):
        self.record["response"]["hourly"]["snow_depth"][0] = 33.33
        self.write_record()
        result = checker.check_file(self.file, self.reference, 5.0)
        self.assertIn("snow_depth outside broad physical bounds", result["errors"])


    def make_complete_season(self):
        expected = checker.expected_hourly_times(*checker.season_dates("2023-2024"))
        self.record["response"]["hourly"]["time"] = [t.strftime("%Y-%m-%dT%H:%M") for t in expected]
        for variable in checker.VARIABLE_LIMITS:
            value = 0.5 if variable == "snow_depth" else 0.0
            self.record["response"]["hourly"][variable] = [value] * len(expected)

    def test_grid_distance_is_non_fatal_spatial_review_warning(self):
        self.make_complete_season()
        self.record["response"]["latitude"] = 46.2
        self.record["response"]["longitude"] = 7.2
        self.write_record()
        result = checker.check_file(self.file, self.reference, 5.0)
        self.assertEqual(result["status"], "WARN")
        self.assertEqual(result["errors"], "")
        self.assertEqual(result["grid_location_review"], "REVIEW_REQUIRED")
        self.assertIn("review its spatial relationship", result["warnings"])
        self.assertIn("not a data-validation failure", result["warnings"])

    def test_snowfall_temperature_flag_is_diagnostic_and_does_not_modify_data(self):
        self.make_complete_season()
        count = len(self.record["response"]["hourly"]["time"])
        self.record["response"]["hourly"]["temperature_2m"] = [3.5] * count
        self.record["response"]["hourly"]["snowfall"] = [0.07] * count
        self.write_record()
        before = self.file.read_bytes()
        result = checker.check_file(self.file, self.reference, 5.0)
        after = self.file.read_bytes()
        self.assertEqual(result["status"], "WARN")
        self.assertEqual(result["errors"], "")
        self.assertEqual(result["snowfall_above_2c_hours"], count)
        self.assertIn("diagnostic only", result["warnings"])
        self.assertEqual(before, after)


    def test_main_scopes_missing_checks_to_selected_weather_points(self):
        self.make_complete_season()
        self.write_record()
        # Add a second reference point with no file. Selecting test-area should
        # validate only that point rather than report the unrelated point missing.
        with self.csv_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["other-point", "2", "Other", "Other Area", "46.1", "7.1", "1000", "2000"])
        with patch.object(sys, "argv", [
            "check_weather_data.py",
            "--resorts-csv", str(self.csv_path),
            "--data-dir", str(self.root),
            "--season", "2023-2024",
            "--weather-point-id", "test-area",
            "--output-dir", str(self.root / "report"),
        ]):
            self.assertEqual(checker.main(), 0)

    def test_parent_folder_identifies_point(self):
        del self.record["weather_point_id"]
        self.write_record()
        result = checker.check_file(self.file, self.reference, 5.0)
        self.assertEqual(result["weather_point_id"], "test-area")


if __name__ == "__main__":
    unittest.main()
