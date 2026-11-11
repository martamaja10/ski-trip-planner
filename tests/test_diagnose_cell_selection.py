import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_cell_selection.py"
spec = importlib.util.spec_from_file_location("diagnose_cell_selection", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class DiagnosticHelpersTest(unittest.TestCase):
    def test_haversine_zero_and_known_distance(self):
        self.assertAlmostEqual(mod.haversine_km(46.0, 7.0, 46.0, 7.0), 0.0)
        self.assertGreater(mod.haversine_km(46.0, 7.0, 46.1, 7.0), 11.0)

    def test_daily_aggregation(self):
        times = [datetime(2024, 1, 1, h) for h in range(3)] + [datetime(2024, 1, 2, 0)]
        vals = [1.0, 2.0, None, 4.0]
        dates, means = mod.daily_aggregate(times, vals, "mean")
        self.assertEqual(len(dates), 2)
        self.assertEqual(means, [1.5, 4.0])
        _, totals = mod.daily_aggregate(times, vals, "sum")
        self.assertEqual(totals, [3.0, 4.0])

    def test_consecutive_hours_above_threshold(self):
        self.assertEqual(mod.consecutive_hours_above([0, 9, 10, None, 11, 12, 13], 8), 3)

    def test_request_diff_ignores_cell_selection_only(self):
        left = {"latitude": 46.0, "longitude": 7.0, "cell_selection": "land"}
        right = {"latitude": 46.0, "longitude": 7.0, "cell_selection": "nearest"}
        self.assertEqual(mod.request_differences(left, right), {})
        right["elevation"] = 1800
        self.assertEqual(mod.request_differences(left, right)["elevation"], {"land": None, "nearest": 1800})

    def test_same_grid_centre_weather_difference_is_reported(self):
        left = {"time": ["2024-01-01T00:00", "2024-01-01T01:00"], "temperature_2m": [1, 2]}
        right = {"time": ["2024-01-01T00:00", "2024-01-01T01:00"], "temperature_2m": [1, 2.5]}
        # Other variables absent are represented as all missing and have no paired hours.
        summary = mod.hourly_difference_summary(left, right)
        self.assertFalse(summary["temperature_2m"]["identical"])
        self.assertEqual(summary["temperature_2m"]["max_absolute_difference"], 0.5)

    def test_geojson_bbox_and_geometry_parts(self):
        feature = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[7.0, 46.0], [7.1, 46.1]]},
            "properties": {},
        }
        self.assertEqual(mod.feature_bbox(feature), (7.0, 46.0, 7.1, 46.1))
        self.assertTrue(mod.bbox_intersects((7.05, 46.05, 7.2, 46.2), mod.feature_bbox(feature)))
        self.assertEqual(len(list(mod.flatten_feature_lines(feature))), 1)


if __name__ == "__main__":
    unittest.main()
