#!/bin/bash
# Polymarket Research Data Collection Pipeline
# Purpose: Automated snapshot collection for strategic analysis
# Author: Poe Research System
# Last Updated: 2026-03-31

set -e

# Configuration
RESEARCH_DIR="/home/clawd/claude/openclaw-orchestration/research"
DATA_DIR="$RESEARCH_DIR/data"
SNAPSHOTS_DIR="$DATA_DIR/snapshots"
ARCHIVE_DIR="$DATA_DIR/archive"
LOG_DIR="$DATA_DIR/logs"

# Create directories if they don't exist
mkdir -p "$SNAPSHOTS_DIR" "$ARCHIVE_DIR" "$LOG_DIR"

# Timestamp for this run
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ISO_TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
LOGFILE="$LOG_DIR/collection_$TIMESTAMP.log"

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOGFILE"
}

# Error handling
on_error() {
    log "ERROR: Collection failed at line $1"
    echo "FAILED" > "$SNAPSHOTS_DIR/collection_status_latest.txt"
    exit 1
}
trap 'on_error $LINENO' ERR

log "Starting Polymarket data collection run"

# ============================================================================
# SECTION 1: Leaderboard Snapshot
# ============================================================================

log "Fetching leaderboard (top 500 traders)..."
LEADERBOARD_FILE="$SNAPSHOTS_DIR/leaderboard_${TIMESTAMP}.json"

# Using polymarket-cli if available, fall back to curl
if command -v polymarket-cli &> /dev/null; then
    polymarket-cli leaderboard --limit 500 --output json > "$LEADERBOARD_FILE" 2>&1 || {
        log "WARNING: polymarket-cli failed, trying curl..."
        curl -s "https://data-api.polymarket.com/v1/leaderboard?limit=500" \
            > "$LEADERBOARD_FILE"
    }
else
    log "Using curl to fetch leaderboard..."
    curl -s "https://data-api.polymarket.com/v1/leaderboard?limit=500" \
        > "$LEADERBOARD_FILE"
fi

if [ -f "$LEADERBOARD_FILE" ] && [ -s "$LEADERBOARD_FILE" ]; then
    LEADERBOARD_SIZE=$(wc -c < "$LEADERBOARD_FILE")
    log "✓ Leaderboard snapshot saved (${LEADERBOARD_SIZE} bytes)"
else
    log "ERROR: Leaderboard fetch failed or empty"
    exit 1
fi

# Extract top 10 for quick reference
jq '.leaderboard[]? | select(.rank <= 10) | {rank: .rank, username: .username, pnl_30d: .pnl_30d_change, win_rate: .win_rate}' \
    "$LEADERBOARD_FILE" > "$SNAPSHOTS_DIR/top10_${TIMESTAMP}.json" 2>/dev/null || true

# ============================================================================
# SECTION 2: Activity Feed
# ============================================================================

log "Fetching recent activity..."
ACTIVITY_FILE="$SNAPSHOTS_DIR/activity_${TIMESTAMP}.json"

curl -s "https://data-api.polymarket.com/v1/activity?limit=1000&limit_offset=0" \
    > "$ACTIVITY_FILE" 2>&1 || {
    log "WARNING: Activity fetch failed (non-critical)"
    echo "{}" > "$ACTIVITY_FILE"
}

if [ -f "$ACTIVITY_FILE" ] && [ -s "$ACTIVITY_FILE" ]; then
    ACTIVITY_SIZE=$(wc -c < "$ACTIVITY_FILE")
    log "✓ Activity snapshot saved (${ACTIVITY_SIZE} bytes)"
else
    log "WARNING: Activity snapshot empty or missing"
fi

# ============================================================================
# SECTION 3: Market Summary (High-Volume Markets)
# ============================================================================

log "Fetching high-volume market summary..."
MARKETS_FILE="$SNAPSHOTS_DIR/markets_${TIMESTAMP}.json"

# Fetch markets endpoint (note: structure may vary)
curl -s "https://polymarket.com/api/markets?order_by=volume_24h&limit=50" \
    > "$MARKETS_FILE" 2>&1 || {
    log "WARNING: Markets fetch failed (non-critical)"
    echo "[]" > "$MARKETS_FILE"
}

if [ -f "$MARKETS_FILE" ] && [ -s "$MARKETS_FILE" ]; then
    MARKETS_SIZE=$(wc -c < "$MARKETS_FILE")
    log "✓ Markets snapshot saved (${MARKETS_SIZE} bytes)"
else
    log "WARNING: Markets snapshot empty"
fi

# ============================================================================
# SECTION 4: Time-Series Aggregation
# ============================================================================

log "Updating time-series aggregates..."

# Update daily leaderboard cache
DAILY_CACHE="$DATA_DIR/leaderboard_timeseries.jsonl"
{
    echo "{\"timestamp\": \"$ISO_TIMESTAMP\", \"data\": $(cat "$LEADERBOARD_FILE")}"
} >> "$DAILY_CACHE" 2>/dev/null || true

# Count historical snapshots
SNAPSHOT_COUNT=$(ls -1 "$SNAPSHOTS_DIR"/leaderboard_*.json 2>/dev/null | wc -l)
log "✓ Total leaderboard snapshots: $SNAPSHOT_COUNT"

# ============================================================================
# SECTION 5: Data Quality Checks
# ============================================================================

log "Running quality checks..."

# Check leaderboard integrity
if jq '.leaderboard[]? | select(.rank != null) | .rank' "$LEADERBOARD_FILE" &>/dev/null; then
    LEADERBOARD_RECORDS=$(jq '.leaderboard[] | select(.rank != null)' "$LEADERBOARD_FILE" 2>/dev/null | wc -l)
    log "✓ Leaderboard integrity: $LEADERBOARD_RECORDS valid records"
else
    log "WARNING: Leaderboard JSON structure unexpected"
fi

# Check for stale data (if comparing timestamps)
if [ -f "$SNAPSHOTS_DIR/leaderboard_latest_timestamp.txt" ]; then
    LAST_TIMESTAMP=$(cat "$SNAPSHOTS_DIR/leaderboard_latest_timestamp.txt")
    HOURS_SINCE=$(( ($(date +%s) - $(date -d "$LAST_TIMESTAMP" +%s)) / 3600 ))
    if [ "$HOURS_SINCE" -lt 1 ]; then
        log "WARNING: Data refresh less than 1 hour since last collection"
    fi
fi

# Update latest timestamp
echo "$ISO_TIMESTAMP" > "$SNAPSHOTS_DIR/leaderboard_latest_timestamp.txt"

# ============================================================================
# SECTION 6: Archive Management (Weekly Consolidation)
# ============================================================================

log "Checking archive status..."

# If weekly archive doesn't exist, create it
WEEK_DIR="$ARCHIVE_DIR/week_$(date +%Y_W%V)"
if [ ! -d "$WEEK_DIR" ]; then
    mkdir -p "$WEEK_DIR"
    log "✓ Created new weekly archive: $WEEK_DIR"
fi

# Monthly consolidation (commented out for now)
# MONTH_DIR="$ARCHIVE_DIR/month_$(date +%Y_%m)"
# if [ ! -d "$MONTH_DIR" ]; then mkdir -p "$MONTH_DIR"; fi

# ============================================================================
# SECTION 7: Summary Report
# ============================================================================

echo "SUCCESS" > "$SNAPSHOTS_DIR/collection_status_latest.txt"

SUMMARY_FILE="$LOG_DIR/summary_$TIMESTAMP.json"
cat > "$SUMMARY_FILE" << EOF
{
  "timestamp": "$ISO_TIMESTAMP",
  "collection_id": "$TIMESTAMP",
  "status": "success",
  "files": {
    "leaderboard": "$LEADERBOARD_FILE",
    "activity": "$ACTIVITY_FILE",
    "markets": "$MARKETS_FILE"
  },
  "statistics": {
    "leaderboard_records": $LEADERBOARD_RECORDS,
    "snapshots_total": $SNAPSHOT_COUNT
  },
  "notes": "Standard collection run completed successfully"
}
EOF

log "✓ Summary saved to $SUMMARY_FILE"
log "✓ Data collection completed successfully"
log "Collection time: $(date -d "@$(date +%s)" '+%Y-%m-%d %H:%M:%S')"

exit 0

# ============================================================================
# USAGE INSTRUCTIONS
# ============================================================================
#
# 1. Install dependencies:
#    pip install polymarket-cli
#    (or ensure curl is available)
#
# 2. Test run:
#    bash /home/clawd/claude/openclaw-orchestration/research/data_collection_template.sh
#
# 3. Set up cron job (e.g., daily at 00:30 UTC):
#    30 0 * * * bash /home/clawd/claude/openclaw-orchestration/research/data_collection_template.sh
#
# 4. Monitor logs:
#    tail -f /home/clawd/claude/openclaw-orchestration/research/data/logs/collection_*.log
#
# 5. Query accumulated data:
#    jq '.leaderboard[] | select(.rank <= 10)' \
#      /home/clawd/claude/openclaw-orchestration/research/data/snapshots/leaderboard_*.json
#
# ============================================================================

