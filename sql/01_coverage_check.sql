-- Question: does every weather point and season have complete, gap-free hourly coverage?
-- Each season runs 1 Nov to 30 Apr (4,344 hours, or 4,368 in a leap-year season).
SELECT
    weather_point_id,
    season,
    count(*)                    AS hourly_rows,
    count(DISTINCT timestamp)   AS distinct_timestamps,
    min(timestamp)              AS first_timestamp,
    max(timestamp)              AS last_timestamp,
    count(*) FILTER (WHERE snow_depth_anomaly) AS flagged_snow_depth_rows
FROM hourly
GROUP BY ALL
ORDER BY weather_point_id, season;
