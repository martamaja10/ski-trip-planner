# Ski Trip Planner

Where should I go skiing this weekend? A tool for people in Geneva and the surrounding area that ranks Alpine ski resorts for a weekend trip, given budget, maximum drive time, trip length and expected snow conditions. Predictions are then checked against historical data to measure how well they reflected reality.

## Question

Given a weekend, a budget tier, a maximum drive time from Geneva and a trip length: which resorts rank best, and how well can ski conditions be predicted from data available beforehand?

## Scope (v1)

- About 30 resorts in Switzerland and France, hand-curated in `data/resorts.csv`
- 4-5 winters (Dec-Apr) of daily weather and snow data per resort
- Weekend ski-conditions score predicted from earlier data and compared with a baseline (last known snow depth)
- Ranking evaluated on a held-out winter (time-based split, no random split)
- Lodging: type and availability near each resort (OpenStreetMap) plus a budget tier

Out of scope for v1: live hotel prices, user reviews and ratings, real-time forecasts, a web app.

## Approach

1. Ingest weather and snow data, resort metadata and lodging data
2. Store in a SQL database and explore with SQL (CTEs, window functions)
3. Build features with pandas and train models with scikit-learn pipelines
4. Filter by the user's constraints and rank resorts by predicted conditions
5. Evaluate rankings against realised conditions (NDCG@k / Spearman)

## Success criteria

- [ ] One command reproduces the dataset
- [ ] At least 5 documented SQL queries, each answering a stated question
- [ ] Model compared with the baseline on a held-out winter
- [ ] Ranking evaluated on held-out weekends
- [ ] Limitations documented

## Status

- [x] Phase 0: repository, scope, resort list (in progress)
- [ ] Phase 1: data ingestion
- [ ] Phase 2: SQL layer
- [ ] Phase 3: features and model
- [ ] Phase 4: recommender and evaluation
- [ ] Phase 5: write-up and tests

## Data and attribution

- Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0), used for non-commercial purposes
- Lodging data: (c) OpenStreetMap contributors (ODbL)
- Resort data (altitude, piste length, day-pass price, drive time): official resort websites and maps, with collection dates recorded in `data/resorts.csv`

## Known limitations (to be completed)

- Historical weather data comes from a coarse grid (about 9 km), which is rough in mountain terrain; resort elevation versus grid elevation is used as a feature
- The "ski conditions score" is a transparent formula defined for this project, not an official measure