-- Question: how do destinations compare in each season?
-- Weather-point values are averaged within a destination, so destinations with several
-- ski areas count once. A "snowfall day" has at least 1 cm of snowfall.
WITH per_point AS (
    SELECT
        destination,
        weather_point_id,
        season,
        max(snow_depth_max_m)                       AS peak_snow_depth_m,
        sum(snowfall_cm)                            AS total_snowfall_cm,
        count(*) FILTER (WHERE snowfall_cm >= 1)    AS snowfall_days,
        avg(temp_mean_c)                            AS mean_temp_c
    FROM daily
    GROUP BY ALL
)
SELECT
    destination,
    season,
    round(avg(peak_snow_depth_m), 2)    AS peak_snow_depth_m,
    round(avg(total_snowfall_cm), 1)    AS total_snowfall_cm,
    round(avg(snowfall_days), 1)        AS snowfall_days,
    round(avg(mean_temp_c), 2)          AS mean_temp_c
FROM per_point
GROUP BY ALL
ORDER BY season, peak_snow_depth_m DESC NULLS LAST;
