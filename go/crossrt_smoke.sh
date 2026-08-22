#!/usr/bin/env bash
# Cross-runtime pack round trip: Python export → Go import → Go export →
# Python import. The "export then import success" gate for the Go port —
# proves the learning data survives a runtime swap in BOTH directions,
# with each hop verified by the RECEIVING runtime's own hash gates and
# read back by its own loaders.
#
# Usage: bash crossrt_smoke.sh [workdir]
# Needs: the Python repo at ../.. (or $MARO_PY_REPO), Go binary built here.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PYREPO="${MARO_PY_REPO:-$(cd "$HERE/.." && pwd)}"
WORK="${1:-$(mktemp -d /tmp/maro-crossrt.XXXXXX)}"
GO_BIN="${GO_BIN:-$HOME/.local/bin/go}"

echo "== workdir: $WORK"
mkdir -p "$WORK/pyws/memory/long" "$WORK/pyws/skills" "$WORK/gows/memory" \
  "$WORK/pyws2/memory" "$WORK/packs"

# --- fixture Python-side workspace ---------------------------------------
cat > "$WORK/pyws/memory/standing_rules.jsonl" <<'EOF'
{"rule_id": "r-001", "rule": "Verify each adversarial finding's code claim before fixing it", "domain": "review", "confirmations": 7, "contradictions": 0, "first_seen": "2026-07-01", "last_seen": "2026-08-01", "imported": {}}
EOF
cat > "$WORK/pyws/memory/hypotheses.jsonl" <<'EOF'
{"hyp_id": "h-100", "lesson": "Long test suites tip the box over unless throttled", "domain": "ops", "confirmations": 2, "contradictions": 0, "source_lesson_ids": [], "first_seen": "2026-08-01", "last_seen": "2026-08-15", "imported": {}}
EOF
cat > "$WORK/pyws/memory/long/lessons.jsonl" <<'EOF'
{"lesson_id": "l-aaa", "task_type": "build", "outcome": "done", "lesson": "Porting a recently-hardened function from memory of its shape reintroduces the pre-fix bug", "source_goal": "port maro to golang", "confidence": 0.9, "tier": "long", "score": 1.4, "last_reinforced": "2026-08-20", "sessions_validated": 4, "times_applied": 6, "times_reinforced": 5, "recorded_at": "2026-08-14T10:00:00+00:00", "acquired_for": null, "evidence_sources": [], "lesson_type": "execution", "imported": {}, "novelty": 0.4, "provisional": false, "minted_from": "outcome", "minted_by": "", "scope": "method", "contested": {}, "merged_variants": ["Diff the sibling's fix history, not just its signature"], "delta_evidence": {}, "grounding": [], "canon": {}}
{"lesson_id": "l-bbb", "task_type": "ops", "outcome": "done", "lesson": "The prompt explicitly says to never stop early, treat that as a hard constraint", "source_goal": "do the thing", "confidence": 0.8, "tier": "long", "score": 1.0, "last_reinforced": "2026-08-01", "sessions_validated": 3, "times_applied": 1, "times_reinforced": 2, "recorded_at": "2026-08-01T10:00:00+00:00", "acquired_for": null, "evidence_sources": [], "lesson_type": "", "imported": {}, "novelty": 0.1, "provisional": false, "minted_from": "prompt", "minted_by": "", "scope": "", "contested": {}, "merged_variants": [], "delta_evidence": {}, "grounding": [], "canon": {}}
EOF
printf '# Triage CI\nRead logs bottom-up. Token sk-ant-abcdefghijklmnop1234 must not ship.\n' \
  > "$WORK/pyws/skills/triage-ci.md"

# --- 1. Python export + seal ---------------------------------------------
( cd "$PYREPO" && PYTHONPATH=src python3 - "$WORK" <<'PYEOF'
import sys
from pathlib import Path
import pack
work = Path(sys.argv[1])
res = pack.export_pack(name="xrt-py", label="pyws", workspace=work / "pyws",
                       out_dir=work / "packs")
pack.seal_pack(Path(res["pack_path"]), confirmed=True)
print("py export+seal OK:", res["pack_path"])
PYEOF
)

# --- 2. Go import (verifies the Python seal) ------------------------------
( cd "$HERE" && "$GO_BIN" build -o "$WORK/maro-go" ./cmd/maro )
"$WORK/maro-go" pack import -pack "$WORK/packs/xrt-py.maropack.tar.gz" \
  -label pyws -target "$WORK/gows" > "$WORK/go-import.json"
grep -q '"outcome": "demoted_to_hypothesis"' "$WORK/go-import.json"
grep -q '"outcome": "imported_medium"' "$WORK/go-import.json"
echo "go import OK (rules demoted, lessons landed medium)"

# --- 3. Python's own readers parse the Go-written rows --------------------
( cd "$PYREPO" && MARO_WORKSPACE="$WORK/gows" PYTHONPATH=src python3 - <<'PYEOF'
from knowledge_lens import load_hypotheses
from knowledge_web import load_tiered_lessons, MemoryTier
hyps = load_hypotheses()
lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
assert len(hyps) == 2, hyps
assert all(h.confirmations == 0 for h in hyps), "trust not reset"
assert len(lessons) == 1, lessons  # prompt-minted lesson dropped at export
l = lessons[0]
assert l.score == 0.5 and l.sessions_validated == 0, "demotion failed"
assert l.merged_variants, "rationale lost in transport"
print("python readers on Go-written store OK")
PYEOF
)

# --- 4. Go export + seal from the Go workspace ----------------------------
"$WORK/maro-go" pack export -name xrt-go -label gows -workspace "$WORK/gows" \
  -out "$WORK/packs" -include-medium
"$WORK/maro-go" pack seal -pack "$WORK/packs/xrt-go.maropack.tar.gz" -yes

# --- 5. Python import (verifies the Go seal — canonical-digest parity) ----
( cd "$PYREPO" && PYTHONPATH=src python3 - "$WORK" <<'PYEOF'
import sys
from pathlib import Path
import pack
work = Path(sys.argv[1])
report = pack.import_pack(work / "packs" / "xrt-go.maropack.tar.gz",
                          label="go-runtime", target=work / "pyws2")
assert report["hypotheses_imported"], report
assert any(r["outcome"] == "imported_medium" for r in report["lessons_imported"]), report
print("python import of Go-sealed pack OK — full circle closed")
PYEOF
)

echo "== CROSS-RUNTIME ROUND TRIP: PASS ($WORK)"
