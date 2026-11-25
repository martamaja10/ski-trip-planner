-- Question: how much snow fell recently, and how cold has it been?
-- Rolling features per weather point. Windows are partitioned by season so they never
-- span the gap between the end of one season and the start of the next.
SELECT
    destination,
    weather_point_id,
    season,
    date,
    snowfall_cm,
    sum(snowfall_cm) OVER w3                AS snowfall_3d_cm,
    sum(snowfall_cm) OVER w7                AS snowfall_7d_cm,
    avg(temp_mean_c) OVER w7                AS temp_mean_7d_c,
    count(*) OVER w7                        AS days_in_7d_window,
    snow_depth_max_m
FROM daily
WINDOW
    w3 AS (PARTITION BY weather_point_id, season ORDER BY date ROWS BETWEEN 2 PRECEDING AND CURRENT ROW),
    w7 AS (PARTITION BY weather_point_id, season ORDER BY date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
ORDER BY weather_point_id, date;
