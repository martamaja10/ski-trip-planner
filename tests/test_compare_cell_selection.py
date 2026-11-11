import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import compare_cell_selection as compare


class CompareCellSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ref = {
            "test-point": {
                "weather_point_id": "test-point", "name": "Test destination",
                "ski_area_name": "Test ski area", "_lat": 46.0, "_lon": 7.0,
            }
        }
        self.land_path = self.root / "land" / "test-point_test" / "2023-2024.json"
        self.nearest_path = self.root / "nearest" / "test-point_test" / "2023-2024.json"
        self.land_path.parent.mkdir(parents=True)
        self.nearest_path.parent.mkdir(parents=True)
        self.times = ["2023-11-01T00:00", "2023-11-01T01:00", "2023-11-01T02:00"]
        self.land = self.make_record("land", 46.05, 7.05, 1500, [0.0, 1.0, 2.0])
        self.nearest = self.make_record("nearest", 46.01, 7.01, 1500, [0.0, 2.0, 2.0])
        self.write_records()

    def tearDown(self):
        self.tmp.cleanup()

    def make_record(self, selection, lat, lon, elevation, temps):
        return {
            "weather_point_id": "test-point",
            "season": "2023-2024",
            "request": {"params": {
                "latitude": 46.0, "longitude": 7.0,
                "start_date": "2023-11-01", "end_date": "2024-04-30",
                "timezone": "UTC",
                "hourly": "temperature_2m,precipitation,snowfall,snow_depth,wind_speed_10m",
                "cell_selection": selection,
            }},
            "response": {
                "latitude": lat, "longitude": lon, "elevation": elevation,
                "hourly": {
                    "time": list(self.times),
                    "temperature_2m": temps,
                    "precipitation": [0.0, 1.0, 0.0],
                    "snowfall": [0.0, 0.5, 0.0],
                    "snow_depth": [0.2, 0.3, 0.3],
                    "wind_speed_10m": [5.0, 6.0, 7.0],
                },
            },
        }

    def write_records(self):
        self.land_path.write_text(json.dumps(self.land), encoding="utf-8")
        self.nearest_path.write_text(json.dumps(self.nearest), encoding="utf-8")

    def test_comparison_calculates_distance_and_hourly_differences(self):
        result = compare.compare_point(
            "test-point", self.ref["test-point"], self.land_path, self.nearest_path, "2023-2024"
        )
        self.assertEqual(result["status"], "PASS")
        self.assertGreater(result["distance_improvement_km_land_minus_nearest"], 0)
        self.assertEqual(result["dem_elevation_difference_m_nearest_minus_land"], 0.0)
        self.assertEqual(result["temperature_2m_mean_signed_difference_nearest_minus_land_°C"], 0.3333)
        self.assertEqual(result["temperature_2m_mean_absolute_difference_°C"], 0.3333)
        self.assertEqual(result["hourly_records_compared"], 3)

    def test_missing_nearest_file_is_reported(self):
        result = compare.compare_point(
            "test-point", self.ref["test-point"], self.land_path, None, "2023-2024"
        )
        self.assertEqual(result["status"], "MISSING")
        self.assertIn("Missing nearest response", result["issues"])

    def test_wrong_selection_method_is_reported(self):
        self.nearest["request"]["params"]["cell_selection"] = "land"
        self.write_records()
        result = compare.compare_point(
            "test-point", self.ref["test-point"], self.land_path, self.nearest_path, "2023-2024"
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("Expected cell_selection='nearest'", result["issues"])

    def test_mismatched_hourly_timestamps_are_reported(self):
        self.nearest["response"]["hourly"]["time"][1] = "2023-11-01T03:00"
        self.write_records()
        result = compare.compare_point(
            "test-point", self.ref["test-point"], self.land_path, self.nearest_path, "2023-2024"
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("Hourly timestamps differ", result["issues"])


if __name__ == "__main__":
    unittest.main()
