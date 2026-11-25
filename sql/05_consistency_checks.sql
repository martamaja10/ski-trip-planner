-- Question: do the SQL aggregates agree with the hourly data they come from?
-- Every column below should be true.
SELECT
    (SELECT count(*) FROM hourly) = 24 * (SELECT count(*) FROM daily)
        AS hourly_rows_equal_24_per_day,
    abs((SELECT sum(snowfall)    FROM hourly) - (SELECT sum(snowfall_cm) FROM daily)) < 1e-6
        AS total_snowfall_matches,
    abs((SELECT sum(precipitation) FROM hourly) - (SELECT sum(precip_mm) FROM daily)) < 1e-6
        AS total_precipitation_matches,
    (SELECT count(*) FROM (SELECT DISTINCT weather_point_id, CAST(timestamp AS DATE) FROM hourly))
        = (SELECT count(*) FROM daily)
        AS one_daily_row_per_point_and_date,
    (SELECT count(*) FROM hourly WHERE snow_depth_anomaly)
        = (SELECT count(*) FROM hourly WHERE snow_depth > 10)
        AS flag_matches_10m_threshold;
