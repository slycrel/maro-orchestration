#!/usr/bin/env bash
# Re-seed twitter-cli's ClientTransaction cache from a plain-curl fetch.
#
# Why this exists (2026-07-11): twitter-cli (0.8.5) initializes X's required
# x-client-transaction-id header by fetching https://x.com via curl_cffi and
# parsing the ondemand.s bundle URL out of the home page. X now serves a
# webpack chunk-id-map page variant that the x_client_transaction lib can't
# parse ("'NoneType' object has no attribute 'split'"), so every GraphQL call
# 404s. A plain curl with a desktop UA gets a parseable page, and the lib
# accepts cached inputs verbatim — so we fetch with curl, resolve the
# ondemand.s hash from the chunk map (id 59924), and write the lib's cache
# file directly. Cache TTL inside the lib is 1h; run this before (and during
# long) X research sessions.
#
# Usage: bash scripts/x-ct-reseed.sh
set -euo pipefail

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -sf -A "$UA" --max-time 20 https://x.com -o "$TMP/home.html"

HASH=$(python3 - "$TMP/home.html" <<'EOF'
import re, sys
html = open(sys.argv[1]).read()
# Old format: "ondemand.s":"<hash>" — new format: chunk-id map, ondemand.s is id 59924
m = re.search(r'["\']ondemand\.s["\']:\s*["\'](\w+)["\']', html)
if not m:
    ids = re.findall(r'(\d+):"ondemand\.s"', html)
    if ids:
        m = re.search(rf'"?{ids[0]}"?:\s*"(\w+)"', html.split(f'{ids[0]}:"ondemand.s"', 1)[1])
    else:
        m = re.search(r'"?59924"?:\s*"(\w+)"', html)
print(m.group(1) if m else "")
EOF
)
[ -n "$HASH" ] || { echo "FAIL: could not extract ondemand.s hash from x.com home page" >&2; exit 1; }

curl -sf -A "$UA" --max-time 20 \
  "https://abs.twimg.com/responsive-web/client-web/ondemand.s.${HASH}a.js" -o "$TMP/ondemand.js"

python3 - "$TMP/home.html" "$TMP/ondemand.js" <<'EOF'
import json, os, sys, time
home = open(sys.argv[1]).read()
ond = open(sys.argv[2]).read()
assert len(ond) > 10_000, f"ondemand bundle suspiciously small ({len(ond)} bytes) — probably a 404 body"
path = os.path.expanduser("~/.twitter-cli/transaction_cache.json")
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump({"home_html": home, "ondemand_text": ond, "created_at": time.time()}, open(path, "w"))
print(f"CT cache seeded: home={len(home)}b ondemand={len(ond)}b -> {path}")
EOF
