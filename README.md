# Alpine Ski Trip Planner

A portfolio project exploring a practical question for skiers based in Geneva: **where should I go skiing this weekend?**

Given a target weekend and constraints such as budget, driving time and trip length, the planner will estimate likely ski conditions at candidate destinations and rank them. Historical weather data will be used to develop and evaluate prediction models by simulating forecasts using only information that would have been available at the time and comparing predictions with subsequently observed conditions.

## Project status

**Completed:** resort reference data, historical weather ingestion, archive validation, exploratory data analysis, reproducible Parquet dataset generation, snow-depth anomaly flagging, and SQL-based aggregation, feature engineering and consistency checks.

**Next:** further data-quality checks and learned anomaly detection, feature engineering, predictive modelling, destination ranking and accommodation matching.

## Project scope

- **Geography:** ski destinations in Switzerland, France and Italy.
- **Reference data:** 18 destinations represented by 21 ski-area weather points.
- **Weather period:** five winter seasons, 2021–2022 through 2025–2026.
- **Weather variables:** hourly temperature, precipitation, snowfall, snow depth and wind speed.
- **Trip constraints:** budget tier, maximum driving time from Geneva and trip length.
- **Planned extension:** accommodation type and availability near each destination.

The first version will not include live hotel prices, user reviews or ratings, real-time forecasts, or a public web application.

## Data sources and location selection

The curated resort reference dataset is maintained in `data/reference/resorts.csv`. A destination can contain multiple distinct ski areas, so the dataset contains 21 weather points across 18 destinations.

Ski-area weather locations were selected using OpenSkiData ski-area and downhill-run geometry. Matching uses normalized names, curated aliases and country constraints; fuzzy matches are treated as suggestions rather than accepted automatically. Candidate locations were generated from matched ski-area features and associated downhill runs, then visually reviewed against piste maps.

For Zermatt, Engelberg–Titlis and Avoriaz, no sufficiently reliable OpenSkiData feature match was found. Their existing reference coordinates were retained and marked as unresolved rather than replaced with speculative matches.

## Data pipeline

The current data workflow is:

1. Maintain the destination and weather-point reference data.
2. Generate and review ski-area weather-point candidates using OpenSkiData geometry.
3. Ingest historical hourly weather from the Open-Meteo Historical Weather API, caching raw responses by weather point and season.
4. Validate cached responses against the reference data and expected seasonal coverage, including timestamps, requested variables, missing values, grid-cell location and elevation diagnostics.
5. Explore the weather archive in the Jupyter notebook.
6. Build the structured hourly dataset with `src/ski_planner/build_dataset.py`: read every cached response, check that the requests used UTC, build one tidy table with source-file provenance, validate the schema, write Parquet, then reload the Parquet file and compare it with the raw JSON (row counts, timestamps and exact values).
7. Flag implausible snow-depth observations without altering the original raw responses; export the flagged observations separately for review.

## Dataset

The historical archive contains:

- **105** weather-point/season files (21 weather points across 5 seasons)
- **456,624** hourly observations
- **18** destinations
- **21** weather points
- **5** winter seasons (2021–2022 to 2025–2026)
- No missing hourly values found in the validated archive

The processed hourly dataset is stored at `data/processed/weather_hourly.parquet`. The flagged snow-depth observations are stored separately at `data/processed/flagged_snow_depth_anomalies.parquet`.

The processed dataset includes destination/weather-point and season information, timestamps, weather variables, source-file provenance (`source_file`) and a snow-depth anomaly flag. Each observation's `source_file` records the original JSON response filename so it can be traced back to its source. The timestamps are stored as UTC; the original Open-Meteo requests were made with `timezone=UTC`. The five weather variables are:

| Variable | Unit |
|---|---|
| Temperature (`temperature_2m`) | °C |
| Precipitation (`precipitation`) | mm |
| Snowfall (`snowfall`) | cm |
| Snow depth (`snow_depth`) | m |
| Wind speed (`wind_speed_10m`) | km/h |

## Validation and data quality

Automated checks cover expected hourly record counts and seasonal timestamp ranges, missing values, requested/reference coordinate consistency, response metadata, weather-variable values and grid-cell location/elevation diagnostics. Validation reports are written under `data/interim/open_meteo_validation/`.

Spatial offsets greater than 5 km are reported as review warnings rather than automatic reasons to discard a series. Diagnostic snowfall/temperature combinations are also retained for review and do not automatically remove observations.

### Snow-depth anomaly

Validation and exploration identified an implausible snow-depth value of 33.33 m at Zermatt:

2024–2025: the value occurs during part of January 2025.

2025–2026: the same value is present throughout the season.

These appear to be source/model artefacts, although their cause has not been independently confirmed. The raw responses are preserved.

A general rule flags snow-depth observations above 10 m at any destination using a project-defined screening threshold. A total of 4,513 hourly observations (approximately 0.99% of the dataset) were flagged and exported to a separate Parquet file (`flagged_snow_depth_anomalies.parquet`) for further investigation, while the original dataset was preserved. All flagged observations are at Zermatt: 4,510 of them are exactly 33.33 m and three are intermediate values (11.33, 18.67 and 26.00 m).

The threshold is a screening heuristic, not a scientifically validated snow-depth limit. Downstream features should treat flagged snow-depth values as unavailable or exclude them as appropriate. Other weather variables are retained. Additional checks for abrupt jumps and flat-lined stretches are planned as further data-quality diagnostics.

## Exploratory analysis

Exploration in `notebooks/01_weather_data_exploration.ipynb` covered data completeness and distributions, seasonality, destination-level comparisons and relationships between weather variables. Key observations include:


- Precipitation and snowfall are strongly skewed, with many zero-observation days and occasional large events; recent multi-day snowfall totals may be more informative than simple averages.
- Snow depth generally builds through winter, with a seasonal peak around March and a slight decline in April.
- Snow-depth and snowfall rankings differ between destinations, so snowfall alone is not a proxy for snow conditions.
- Correlations calculated across all resorts and seasons should be interpreted cautiously because they combine destinations, elevations and time periods.

## SQL Analytics & Data Quality

The processed weather dataset is analysed using DuckDB and SQL, providing a reproducible analytical layer on top of the hourly Parquet data.

Five SQL queries cover the main analytical steps:

- **Coverage validation:** Checks hourly data completeness for each weather point and ski season, identifying missing hours, duplicate timestamps, and deviations from expected date boundaries.
- **Daily aggregation:** Aggregates hourly weather observations into daily metrics, including temperature, precipitation, snowfall, snow depth and wind.
- **Rolling weather features:** Calculates rolling 3-day and 7-day snowfall, 7-day average temperature, and snow-depth features to capture recent weather conditions.
- **Destination comparison:** Summarises seasonal weather metrics to enable comparisons between ski destinations.
- **Consistency checks:** Validates aggregation results against the hourly dataset and verifies data-quality flag logic.

The SQL workflows are executed using DuckDB against Parquet datasets, with query results exported to CSV for downstream analysis.

The pipeline includes consistency checks to verify aggregation accuracy and data integrity. 

## Limitations

- Historical weather comes from a gridded model, not measurements directly on ski pistes. A single weather point cannot represent all elevation changes or microclimates across a large ski area.
- OpenSkiData coverage and elevation information vary. Unresolved matches remain visible in the reference data rather than being replaced with speculative matches.
- The future ski-conditions score will be a transparent, project-defined measure, not an official resort or snow-quality rating.
- Historical model performance does not guarantee the same accuracy for future conditions.
- Accommodation availability and live prices are not part of the first version.

## Repository structure

```text
data/
  reference/
    resorts.csv
  external/
    openskidata/
  raw/
    open-meteo/                 # cached API responses; git-ignored
  interim/
    open_meteo_validation/      # validation reports
    weather_point_validation/   # candidate/review outputs
  processed/                    # the processed files are committed for convenience; see Build the processed dataset to regenerate them.
    weather_hourly.parquet
    flagged_snow_depth_anomalies.parquet
scripts/
  validate_weather_points.py
  check_weather_data.py
src/
  ski_planner/
    ingest.py
    build_dataset.py            # raw JSON -> validated Parquet dataset
notebooks/
  01_weather_data_exploration.ipynb
tests/
  test_build_dataset.py
requirements.txt
```

## Setup

From the repository root:


```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

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

The checker writes CSV and JSON reports under `data/interim/open_meteo_validation/`. Spatial offsets above 5 km are reported as non-fatal warnings; the checker exits with an error for failed or missing results.

### Build the processed dataset

```bash
python src/ski_planner/build_dataset.py
```

By default this reads `data/raw/open-meteo/` and writes `data/processed/weather_hourly.parquet` and `data/processed/flagged_snow_depth_anomalies.parquet` (use `--raw-dir` and `--out-dir` to change the locations). The script stops if a request was not made with `timezone=UTC`, if the schema is wrong, or if the Parquet file it wrote does not match the raw responses. Raw files are never modified. The notebook loads the resulting Parquet file instead of rebuilding it.

The processed Parquet files are committed for convenience. They are derived from the raw responses, which are not committed (data/raw/), so regenerating them takes two steps: ingest the five seasons (see Ingest historical weather), then run python src/ski_planner/build_dataset.py.

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

1. Extend the data-quality checks (abrupt jumps and flat-lined stretches), then compare the rule-based flags with a learned anomaly detector on the hourly series, using the Zermatt observations as a known test case.
2. Develop prediction features from historical weather data (e.g. recent snowfall, snow depth and temperature). Train models to estimate future ski conditions and evaluate performance on a winter season not used during training, comparing against simple baseline predictions.
3. Develop destination filtering and ranking logic, then evaluate rankings against realised weekend conditions.
4. Add accommodation data and complete the project write-up.

## Data sources and attribution

- **Weather:** [Open-Meteo Historical Weather API](https://open-meteo.com/). Follow the applicable API terms and attribution requirements when publishing results.
- **Ski-area and piste geometry:** OpenSkiData / OpenSkiMap GeoJSON. Preserve source/version information and check the applicable licence and attribution requirements.
- **Resort metadata:** official resort websites and piste maps; source notes and collection dates are recorded in the reference dataset.
- **Accommodation data (planned):** OpenStreetMap contributors; comply with ODbL attribution requirements when using or publishing derived data.
