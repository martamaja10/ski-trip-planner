import unittest

from shapely.geometry import Point, mapping

from scripts.validate_weather_points import match_ski_area, validate_one, validate_reference_csv


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.grands_montets = {
            "type": "Feature",
            "id": "gm-operating",
            "geometry": mapping(Point(6.9, 45.95)),
            "properties": {
                "id": "gm-operating",
                "name": "Les Grands Montets",
                "status": "operating",
                "places": [{"iso3166_1Alpha2": "FR"}],
            },
        }
        self.abandoned = {
            "type": "Feature",
            "id": "argent-fis",
            "geometry": mapping(Point(6.9267, 45.9846)),
            "properties": {
                "id": "argent-fis",
                "name": "L’aiguillette Argentière FIS",
                "status": "abandoned",
                "places": [{"iso3166_1Alpha2": "FR"}],
            },
        }

    def test_exact_grands_montets_match_wins_over_abandoned_near_name(self):
        match = match_ski_area(
            [self.abandoned, self.grands_montets],
            "Les Grands Montets",
            [],
            "FR",
        )
        self.assertEqual(match["name"], "Les Grands Montets")
        self.assertEqual(match["status"], "operating")

    def test_broad_argentiere_name_is_not_accepted_as_alias(self):
        with self.assertRaisesRegex(ValueError, "no_exact_match"):
            match_ski_area([self.abandoned], "Les Grands Montets", [], "FR")

    def test_4_vallees_full_domain_alias_is_exact(self):
        feature = {
            "type": "Feature",
            "id": "4v",
            "geometry": mapping(Point(7.3, 46.12)),
            "properties": {
                "id": "4v",
                "name": "4 Vallées – Verbier/La Tzoumaz/Nendaz/Veysonnaz/Thyon",
                "status": "operating",
                "places": [{"iso3166_1Alpha2": "CH"}],
            },
        }
        match = match_ski_area(
            [feature],
            "4 Vallées",
            ["4 Vallées – Verbier/La Tzoumaz/Nendaz/Veysonnaz/Thyon"],
            "CH",
        )
        self.assertEqual(match["name"], feature["properties"]["name"])

    def test_spatial_nearby_runs_do_not_create_candidate_without_association(self):
        area = {
            "type": "Feature",
            "id": "area-1",
            "geometry": mapping(Point(7.0, 46.0)),
            "properties": {
                "id": "area-1",
                "name": "Test Ski Area",
                "status": "operating",
                "places": [{"iso3166_1Alpha2": "CH"}],
            },
        }
        # This run is nearby but carries no association identifier.
        run = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[7.0, 46.0, 1500], [7.001, 46.001, 1400]]},
            "properties": {"uses": ["downhill"]},
        }
        row = {
            "weather_point_id": "test-1",
            "name": "Test",
            "ski_area_name": "Test Ski Area",
            "country": "CH",
            "lat": 46.0,
            "lon": 7.0,
        }
        result, audit = validate_one(row, [area], [run], min_run_count=1, buffer_km=8)
        self.assertEqual(result["validation_status"], "insufficient_explicitly_associated_runs")
        self.assertEqual(result["associated_run_count"], 0)
        self.assertGreater(result["nearby_spatial_run_count_diagnostic_only"], 0)
        self.assertNotIn("candidate_lat", result)


class ReferenceCsvTests(unittest.TestCase):
    def test_current_reference_csv_passes_project_integrity_checks(self):
        import pandas as pd
        from pathlib import Path

        csv_path = Path(__file__).parents[1] / "data" / "reference" / "resorts.csv"
        frame = pd.read_csv(csv_path, encoding="utf-8-sig")
        checked = validate_reference_csv(frame)
        self.assertEqual(len(checked), 21)
        self.assertEqual(checked["destination_id"].nunique(), 18)
        self.assertEqual(set(checked["destination_id"].astype(int)), set(range(1, 19)))
        self.assertNotIn("source_resort_id", checked.columns)


if __name__ == "__main__":
    unittest.main()
