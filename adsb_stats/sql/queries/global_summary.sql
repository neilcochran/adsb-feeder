-- Current all-time global statistics.
SELECT msg_total, uaircraft_total, uflights_total,
       alt_max, alt_max_icao, alt_max_ts,
       dist_max_nm, dist_max_icao, dist_max_ts,
       first_msg_ts, last_msg_ts,
       error_count, last_error_ts, last_error_msg
FROM global_stats
WHERE id = 1;
