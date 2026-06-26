# Orchestration Test Corpus

Real orchestration history distilled into replayable fixture slices, harvested
from `~/.maro/workspace/runs/` + `projects/` by `scripts/harvest_corpus.py`.
These are **dev fixtures**, not runtime data — they let tests drive the
post-LLM machinery (verify gating, quality-gate escalation, claim probing,
stuck classification, hallucinated-file detection, decompose shapes) against
inputs/outputs that actually occurred instead of hand-built stubs.

## What's here

- `MANIFEST.md` — regenerated counts per slice (records / unique signatures / thinned).
- `<slice>.thinned.jsonl` — **committed.** One+ representative per normalized
  signature, with `_cluster_size` (how many real occurrences it stands in for).
- `<slice>.jsonl` — **git-ignored.** Full records; regenerate locally with
  `python3 scripts/harvest_corpus.py`.

Every record carries `_provenance` (path under the workspace it came from).
Credential-shaped strings are scrubbed at harvest; capture-time
`[REDACTED:EXFIL_ATTEMPT]` markers are Maro's own exfil protection, preserved.

## What is NOT here (and why)

- **Byte-exact per-step LLM prompt + raw response** — never persisted. We have
  the input goal (`source/prompt.txt`) and a truncated `result_excerpt`, not the
  assembled per-step prompt or full response. So these fixtures support
  *logic-level* replay, not byte-level LLM mocking.
- **Inner tool_events** — the `claude -p` tool-call transcript capture is new
  (shipped 2026-06-26); only future runs produce it. Forward byte-level replay
  mocks are the "later" half of this work.

## Using a slice in a test

```python
import json, pathlib
CORPUS = pathlib.Path(__file__).parent / "fixtures" / "orchestration_corpus"

def load(slice_name):
    p = CORPUS / f"{slice_name}.thinned.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

# e.g. regression-guard the quality-gate escalate formula against real verdicts:
for r in load("event_quality_gate_verdict"):
    c = r["context"]
    assert c["escalate"] == (c["verdict"].upper() == "ESCALATE"
                             and c["confidence"] >= c["confidence_threshold"])
```

See `tests/test_orchestration_corpus.py` for the worked example.

## Refreshing

`python3 scripts/harvest_corpus.py [--keep N] [--dry-run]` re-harvests from the
live workspace. Re-run after accumulating new runs to grow coverage.
