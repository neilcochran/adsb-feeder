-- Top 10 hours by message count, all time.
SELECT ts, msg_count, uaircraft, alt_max, dist_max_nm
FROM hourly_stats
ORDER BY msg_count DESC
LIMIT 10;
