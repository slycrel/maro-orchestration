# review/

Structured ledger of adversarial-review findings — one JSON row per
finding, appended by `scripts/review-ledger.py`.

`docs/REVIEW_PATTERNS.md` is the lens catalog: it names the recurring
*reasons a defect was invisible*. This directory records findings against
those lenses so the recurrence counts stop being recalled and start being
computed.

**Dev-facing tooling.** Same boundary `correspondence` sits on: this is
about the development loop, not Maro's runtime self-improvement. It lives
in the repo — deliberately NOT in `~/.maro/workspace/memory/` — so nothing
in the learning pipeline can ingest a review finding as a run outcome.

- `findings.jsonl` — the ledger. Append-only; never rewritten.
- `backfill.json` — staging for a bulk `import`, kept for provenance.
