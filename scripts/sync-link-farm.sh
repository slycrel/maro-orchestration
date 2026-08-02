#!/usr/bin/env bash
# Sync the link-farm clone and report its vintage.
#
# The link farm (github.com/slycrel/link-farm) is Jeremy's curated reference
# memory — ideas, tech, and concepts he has taken time to identify as
# reference points. It receives near-daily "Sync" commits. The 2026-08-02
# lesson this script exists to prevent: the box's clone sat frozen at 315
# posts (2026-04-11) for months, and a search against it reported six
# on-point articles as absent — they all postdated the snapshot.
#
# Rule (the denominator family): before claiming something is absent from
# this corpus, run this script and check the reported vintage covers the
# period where the thing would exist.
#
# Usage:
#   bash scripts/sync-link-farm.sh              # default clone location
#   LINK_FARM_DIR=/path bash scripts/sync-link-farm.sh
set -euo pipefail

DIR="${LINK_FARM_DIR:-$HOME/claude/link-farm}"

if [ ! -d "$DIR/.git" ]; then
    echo "no link-farm clone at $DIR — clone it first:" >&2
    echo "  git clone https://github.com/slycrel/link-farm \"$DIR\"" >&2
    exit 2
fi

cd "$DIR"
BEFORE="$(git rev-parse --short HEAD)"
# ff-only: this is a read-only mirror of Jeremy's curation; local edits
# don't belong here and a diverged clone should fail loudly, not merge.
git pull --ff-only -q origin "$(git rev-parse --abbrev-ref HEAD)"
AFTER="$(git rev-parse --short HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
    echo "link-farm: already current at $AFTER"
else
    echo "link-farm: updated $BEFORE -> $AFTER"
fi

# Vintage report — the number that gates absence claims.
python3 - <<'PY'
import json, os, sqlite3
d = os.environ.get("LINK_FARM_DIR", os.path.expanduser("~/claude/link-farm"))
db = os.path.join(d, "db", "ai_links.db")
try:
    con = sqlite3.connect(db)
    n, lo, hi = con.execute("select count(*), min(date), max(date) from posts").fetchone()
    print(f"vintage: {n} posts, {lo} .. {hi} (db)")
except Exception:
    try:
        p = json.load(open(os.path.join(d, "posts_final_v3.json")))
        rows = p if isinstance(p, list) else p.get("posts", [])
        ds = sorted(str(r.get("date", "")) for r in rows if r.get("date"))
        print(f"vintage: {len(rows)} posts, {ds[0]} .. {ds[-1]} (json)")
    except Exception as exc:
        print(f"vintage: unreadable ({exc})")
PY
