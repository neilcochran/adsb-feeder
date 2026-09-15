-- Every confirmed emergency squawk event (7500/7600/7700), most recent first.
SELECT first_ts, last_ts, icao, callsign, squawk, msg_count, altitude_ft, dist_nm
FROM emergency_events
ORDER BY first_ts DESC;
