# Alpine Ski Trip Planner

A portfolio project exploring a practical question for skiers based in Geneva: **where should I go skiing this weekend?**

The planner is intended to support decisions about upcoming ski trips. Given a target date or weekend and constraints such as budget, driving time and trip length, it will estimate likely ski conditions at candidate destinations and rank them accordingly. Historical weather data will be used to develop and evaluate the prediction models, by simulating forecasts using only information that would have been available at the time and comparing predictions with subsequently observed conditions.

**Current status:** the reference dataset and five-season historical weather archive are in place. Historical archive ingestion and validation are complete; feature engineering, predictive modelling and destination ranking are the next stages.

## Project scope

- **Geography:** ski destinations in Switzerland, France and Italy.
- **Reference data:** 18 destinations represented by 21 ski-area weather points.
- **Weather period:** five winter seasons, 2021–2022 to 2025–2026.
- **Weather variables:** hourly temperature, precipitation, snowfall, snow depth and wind speed.
- **Trip constraints:** budget tier, maximum driving time from Geneva and trip length.
- **Planned extension:** accommodation type and availability near each destination.

The first version will not include live hotel prices, user reviews or ratings, real-time forecasts, or a public web application.

## Data and methodology

The curated reference dataset is maintained in `data/reference/resorts.csv`. A destination can contain multiple distinct ski areas, so the dataset has 21 weather points across 18 destinations.

Ski-area weather locations were selected using OpenSkiData ski-area and downhill-run geometry. Matching uses normalized names, curated aliases and country constraints; fuzzy matches are treated as suggestions rather than accepted automatically.

For 18 weather points, candidate locations were generated from matched ski-area features and associated downhill runs, then visually reviewed against piste maps. These are marked provisionally_validated.

For Zermatt, Engelberg–Titlis and Avoriaz, no sufficiently reliable OpenSkiData feature match was found. Their existing reference coordinates were retained and marked unresolved_openskidata_match, rather than replaced with speculative matches.

This status concerns the OpenSkiData match only. All 21 weather points, including these three, were included in the five-season Open-Meteo archive and subjected to the same automated data checks. These checks assess archive completeness, response consistency, grid-cell location, DEM elevation and potential data anomalies. They do not independently establish that a requested point perfectly represents conditions across an entire ski area.

## Data quality and limitations

The archive contains 105 weather-point/season files covering all 21 points across five seasons. Validation found complete hourly coverage and no missing values. It also identified two Zermatt snow-depth anomalies:

- **2024–2025:** an implausible reported snow depth of 33.33 m during part of January 2025.
- **2025–2026:** the same 33.33 m value throughout the season.

These appear to be source/model artefacts, although their cause has not been independently confirmed. The raw responses are retained; affected snow-depth values should be marked unavailable or excluded when building downstream features. Other weather variables are retained.

Other validation warnings include grid-cell centres more than 5 km from a requested point and diagnostic snowfall/temperature combinations. These are review flags, not automatic reasons to discard a weather series.

Key limitations:

- Gridded historical weather is not a measurement taken directly on a piste. A single point cannot capture all elevation changes or microclimates across a large ski area.
- OpenSkiData coverage and 3D elevation availability vary. Unresolved matches are kept visible rather than replaced with speculative matches.
- The eventual ski-conditions score will be a transparent, project-defined measure, not an official resort or snow-quality rating.
- Historical model performance does not guarantee the same accuracy for future conditions.

## Repository structure

```text
data/
  reference/resorts.csv
  external/openskidata/
  raw/open-meteo/                 # downloaded API responses; git-ignored
  interim/
scripts/
  validate_weather_points.py
  check_weather_data.py
  compare_cell_selection.py
  diagnose_cell_selection.py
src/
  ski_planner/ingest.py
tests/
requirements.txt
```

## Setup and usage

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Ingest historical weather

The ingestion script processes one winter season at a time. It checks cached responses and reuses them when their metadata and hourly data match the current request.

```bash
for season in 2021 2022 2023 2024 2025; do
  python src/ski_planner/ingest.py \
    --resorts data/reference/resorts.csv \
    --season "$season"
done
```

### Validate the archive

```bash
python scripts/check_weather_data.py \
  --resorts-csv data/reference/resorts.csv \
  --data-dir data/raw/open-meteo \
  --season 2021-2022 \
  --season 2022-2023 \
  --season 2023-2024 \
  --season 2024-2025 \
  --season 2025-2026
```

The checker writes CSV and JSON reports under `data/interim/open_meteo_validation/`. Spatial offsets above 5 km are reported as non-fatal warnings; the checker exits with an error for FAIL or MISSING results.

### Validate ski-area weather-point candidates

```bash
python scripts/validate_weather_points.py \
  --resorts data/reference/resorts.csv \
  --all
```

This uses cached OpenSkiData GeoJSON where available and writes candidate data, an audit and review maps under `data/interim/weather_point_validation/`. Use `--refresh` to download fresh source data.

### Run tests

```bash
python -m unittest discover -s tests -v
```

## Next steps

1. Build a SQL layer and explore the weather archive with documented analytical queries.
2. Develop prediction features from historical weather data (e.g. recent snowfall, snow depth and temperature). Train models to estimate future ski conditions and evaluate their performance on a winter season not used during training, comparing them against simple baseline predictions.
3. Develop destination filtering and ranking logic, then evaluate rankings against realised weekend conditions.
4. Add accommodation data and complete the project write-up.

## Data sources and attribution

- **Weather:** [Open-Meteo Historical Weather API](https://open-meteo.com/). Follow the applicable API terms and attribution requirements when publishing results.
- **Ski-area and piste geometry:** OpenSkiData / OpenSkiMap GeoJSON. Preserve source/version information and check the applicable licence and attribution requirements.
- **Resort metadata:** official resort websites and piste maps; source notes and collection dates are recorded in the reference dataset.
- **Accommodation data (planned):** OpenStreetMap contributors; comply with ODbL attribution requirements when using or publishing derived data.
