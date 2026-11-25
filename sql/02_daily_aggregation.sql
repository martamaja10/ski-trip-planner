-- Question: what does each weather point look like per day?
-- One row per weather point and day. Snow depth ignores observations flagged as
-- anomalous, so a faulty source cannot distort daily values (NULL when all hours are flagged).
CREATE OR REPLACE VIEW daily AS
SELECT
    destination,
    weather_point_id,
    season,
    CAST(timestamp AS DATE)                                     AS date,
    avg(temperature_2m)                                         AS temp_mean_c,
    min(temperature_2m)                                         AS temp_min_c,
    max(temperature_2m)                                         AS temp_max_c,
    sum(precipitation)                                          AS precip_mm,
    sum(snowfall)                                               AS snowfall_cm,
    max(snow_depth) FILTER (WHERE NOT snow_depth_anomaly)       AS snow_depth_max_m,
    max(wind_speed_10m)                                         AS wind_max_kmh
FROM hourly
GROUP BY ALL;

SELECT * FROM daily ORDER BY weather_point_id, date;
