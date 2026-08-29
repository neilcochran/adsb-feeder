-- All daily stats, most recent day first.
SELECT date, msg_count, uaircraft, uflights, alt_max, dist_max_nm
FROM daily_stats
ORDER BY date DESC;
