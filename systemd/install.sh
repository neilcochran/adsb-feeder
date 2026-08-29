#!/usr/bin/env bash
#
# install.sh — Deploy the adsb-stats systemd service and verify the
#              full feeder service set.
#
# Usage:  sudo bash systemd/install.sh
#
# What it does:
#   1. Creates the adsbstats system user (if missing)
#   2. Creates data and config directories
#   3. Copies the repo to /opt/adsb-feeder/
#   4. Installs and enables adsb-stats.service
#   5. Verifies all expected feeder services are present
#
# It will NOT touch upstream service units (dump1090-fa, piaware, etc.)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_FILE="adsb-stats.service"
TARGET_DIR="/opt/adsb-feeder"
DATA_DIR="/var/lib/adsb-stats"
CONFIG_DIR="/etc/adsb-stats"
DB_FILE="${DATA_DIR}/stats.db"
CONFIG_FILE="${CONFIG_DIR}/config.json"

# ── 1. Dedicated user ──────────────────────────────────────────
if ! id adsbstats &>/dev/null; then
    echo "[install] Creating system user 'adsbstats'"
    useradd --system \
        --home-dir "$DATA_DIR" \
        --shell /usr/sbin/nologin \
        --comment "ADS-B Statistics Collector" \
        adsbstats
else
    echo "[install] User 'adsbstats' already exists — skipping"
fi

# ── 2. Directories ────────────────────────────────────────────
for dir in "$TARGET_DIR" "$DATA_DIR" "$CONFIG_DIR"; do
    if [[ ! -d "$dir" ]]; then
        echo "[install] Creating $dir"
        mkdir -p "$dir"
    fi
done

chown -R adsbstats:adsbstats "$DATA_DIR"
chown -R adsbstats:adsbstats "$CONFIG_DIR"
chmod 755 "$TARGET_DIR"
# Group-readable (not world-readable) rather than left at whatever the
# ambient umask produces, so read access is an explicit, intentional grant
# (see the "adsbstats group" step printed at the end of this script).
chmod 750 "$DATA_DIR"

# ── 3. Install code ───────────────────────────────────────────
if [[ -d "$REPO_ROOT/adsb_stats" ]]; then
    echo "[install] Copying repo → $TARGET_DIR/"
    rsync -a --delete "$REPO_ROOT/" "$TARGET_DIR/"
    chown -R root:root "$TARGET_DIR"
    # adsbstats needs read access to the package
    chmod -R a+rX "$TARGET_DIR"
else
    echo "[install] WARNING: $REPO_ROOT/adsb_stats/ not found."
    echo "[install]         The module must be developed before this step."
    echo "[install]         The service file will be installed but the service"
    echo "[install]         will fail to start until the code is present."
fi

# ── 4. Default config (only if not already present) ──────────
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "[install] Creating default config at $CONFIG_FILE"
    cat > "$CONFIG_FILE" <<'JSON'
{
  "sbs_host": "127.0.0.1",
  "sbs_port": 30003,
  "aircraft_json_path": "/run/dump1090-fa/aircraft.json",
  "db_path": "/var/lib/adsb-stats/stats.db",
  "receiver_lat": null,
  "receiver_lon": null,
  "flush_interval_seconds": 300,
  "log_level": "INFO"
}
JSON
    chown adsbstats:adsbstats "$CONFIG_FILE"
    chmod 640 "$CONFIG_FILE"
    echo "[install] Edit $CONFIG_FILE to set receiver_lat/receiver_lon"
    echo "[install]           for distance tracking on the stationary unit."
else
    echo "[install] Config already exists at $CONFIG_FILE — skipping"
fi

# ── 5. Initialize (or migrate) the database ──────────────────
# Always run `init`, even for a pre-existing database: init_db()/
# _migrate_schema() are required to be safe to re-run (see schema.sql's
# header comment) - this is what applies newly-added columns to an
# already-deployed database. Skipping this step here would leave a
# redeployed database missing any migration added since it was created.
if [[ ! -f "$DB_FILE" ]]; then
    echo "[install] Initializing database"
else
    echo "[install] Database already exists at $DB_FILE — checking for schema updates"
fi
su -s /bin/bash adsbstats -c \
    "cd $TARGET_DIR && python3 -m adsb_stats.cli init --config $CONFIG_FILE"
chown adsbstats:adsbstats "$DB_FILE"
chmod 640 "$DB_FILE"

# ── 6. Install systemd unit ──────────────────────────────────
echo "[install] Installing $SERVICE_FILE"
cp "$REPO_ROOT/systemd/$SERVICE_FILE" /etc/systemd/system/
systemctl daemon-reload
systemctl enable adsb-stats

# ── 7. Install CLI wrapper ────────────────────────────────────
if [[ -f "$REPO_ROOT/bin/adsb-stats" ]]; then
    echo "[install] Linking adsb-stats CLI wrapper to /usr/local/bin/"
    chmod +x "$TARGET_DIR/bin/adsb-stats"
    ln -sf "$TARGET_DIR/bin/adsb-stats" /usr/local/bin/adsb-stats
fi

# ── 8. Verify full service set ───────────────────────────────
echo ""
echo "── Service Verification ───────────────────────────────────"

EXPECTED_SERVICES=(
    dump1090-fa
    piaware
    fr24feed
    adsbexchange-feed
    adsbexchange-mlat
    adsb-stats
)

for svc in "${EXPECTED_SERVICES[@]}"; do
    # Check if the unit file exists (may not be installed on mobile)
    if systemctl list-unit-files "$svc.service" &>/dev/null \
       && [[ -n "$(systemctl list-unit-files "$svc.service" 2>/dev/null | grep "$svc")" ]]; then
        state=$(systemctl is-active "$svc" 2>/dev/null || echo "not-running")
        enabled=$(systemctl is-enabled "$svc" 2>/dev/null || echo "not-enabled")
        printf "  %-25s  active: %-10s  enabled: %s\n" "$svc" "$state" "$enabled"
    else
        printf "  %-25s  (not installed — OK on mobile unit)\n" "$svc"
    fi
done

echo ""
echo "[install] Done."
echo "[install] Start the service with:  systemctl start adsb-stats"
echo "[install] Check status with:       systemctl status adsb-stats"
echo "[install] View logs with:          journalctl -u adsb-stats -f"
echo "[install] Query stats directly with:  adsb-stats status"
echo "[install] To let another user read $DB_FILE (e.g. for the terminal"
echo "[install]   monitor's adsb_global/adsb_health sections), run:"
echo "[install]     sudo usermod -aG adsbstats <username>"
echo "[install]   then have them log out and back in."