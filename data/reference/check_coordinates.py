"""Cross-check resort coordinates in resorts.csv against Open-Meteo geocoding.

Usage:  python check_coordinates.py resorts_with_coordinates.csv

For each resort it asks the geocoding API for places with that name in the
same country, picks the one closest to the coordinates in the file, and
reports the distance. It never modifies the CSV.
Geocoding data: Open-Meteo (https://open-meteo.com), GeoNames-based.
"""
import csv
import json
import math
import sys
import time
import urllib.parse
import urllib.request

API = "https://geocoding-api.open-meteo.com/v1/search"
THRESHOLD_KM = 5.0

# Search names where the CSV name is not what a geocoder expects.
SEARCH_NAME = {
    "Crans Montana": "Crans-Montana",
    "Les 2 Alps": "Les Deux Alpes",
    "Les Contamines": "Les Contamines-Montjoie",
}


def haversine_km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(a))


def geocode(name, country):
    params = urllib.parse.urlencode(
        {"name": name, "count": 10, "language": "en", "format": "json", "countryCode": country})
    with urllib.request.urlopen(f"{API}?{params}", timeout=20) as resp:
        return json.load(resp).get("results", [])


def closest(results, lat, lon):
    scored = [(haversine_km(lat, lon, r["latitude"], r["longitude"]), r) for r in results]
    return min(scored, key=lambda t: t[0]) if scored else (None, None)


def main(path):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    print(f"{'resort':<18}{'dist_km':>8}  {'geo_elev_m':>10}  {'csv_base_m':>10}  note")
    for row in rows:
        name = row["name"].strip()
        lat, lon = float(row["lat"]), float(row["lon"])
        try:
            results = geocode(SEARCH_NAME.get(name, name), row["country"].strip())
        except Exception as exc:  # network or API problem
            print(f"{name:<18}{'-':>8}  {'-':>10}  {'-':>10}  ERROR {exc}")
            continue
        dist, best = closest(results, lat, lon)
        if best is None:
            print(f"{name:<18}{'-':>8}  {'-':>10}  {row['base_alt_m']:>10}  no geocoder match, check by hand")
        else:
            note = "OK" if dist <= THRESHOLD_KM else "CHECK: far from geocoder result"
            elev = best.get("elevation")
            print(f"{name:<18}{dist:>8.1f}  {str(elev):>10}  {row['base_alt_m']:>10}  {note}")
        time.sleep(0.5)  # be gentle with the free API


if __name__ == "__main__":
    main(sys.argv[1])
