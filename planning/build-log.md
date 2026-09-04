---
status: living
---

# Successor build log — one entry per build step (design note §16)

Each step: **subtraction artifact** (what was proposed, what requires it,
what was deleted) → build → edge tests → land. Reviews are evidence discovery
on risky slices, not a completion ritual (design note §15).

## Step 1 — records, identity, envelopes, thought store, lease, contracts foundation (2026-09-04)

**Subtraction artifact.** Proposed for this step, with what requires each:

| Item | Required by | Kept? |
|---|---|---|
| `record.Header` (ID, Schema, Seq, RunID, Attempt, Subject, Supersedes, At) | §1a; r1/r2 identity findings | kept |
| ULID allocator (no dependency) | §1a time-ordered IDs; swipe-over-deps rule | kept; 60 lines |
| Three envelope markers + kind registry as the single authority | §1a, §9; r4 finding 4 | kept |
| Registry doubles as the record census (writer/reader/decision/retention) | §14 | kept — one table, not two |
| `thought.Store` content-addressed, private construction, verify-on-read | §1b, D16 | kept |
| blake3 | design note said blake3 | **deleted** → SHA-256 from stdlib, domain-separated, versioned prefix `s256v1` (swipe-over-deps; nothing here needs blake3's speed) |
| `chunk` thought kind | v1.1 design | **deleted** — no engine chunking in v1 (r2 finding C); `Put("chunk")` is refused and tested |
| Workspace root with announce-before-write enforced by type | §13; 2026-08-16 scar | kept |
| Lease with PID + monotonic epoch; stale takeover | §2, D12 | kept |
| Contracts: generated (reflection) + declared (JSON) + report + answer key + census + reference reader + drift gate | §1c; contract-testing-input.md | kept |
| YAML for declared files | work-side practice uses YAML | **deleted** → JSON (no dependency; the vocabulary is what matters, not the syntax) |
| Journal/sequencer, commands, views | §2 | **not this step** (step 2) |
| Interfaces beyond CLI | — | not in v1 |

**Built.** `go/internal/{record,thought,workspace,contracts}`, `go/cmd/maro-go`
(`workspace`, `contracts gen|report|check`), committed pairs under
`go/contracts/` for the two step-1 kinds (`lease`, `thought_stored`) plus the
generated answer key and census.

**Edge tests (all red-first in spirit — each names the failure it exists to
catch).** Records: ULID shape and same-ms monotonicity; SchemaVer parse
refuses malformed; registry refuses two markers / no marker / marker-envelope
disagreement / incomplete census / duplicate kind / duplicate type; header
validation refuses future versions, wrong kind, stored Seq 0, bad Supersedes,
unregistered kinds. Thoughts: whole-body round trip; 8 MiB, empty and
non-UTF-8 bodies flow whole (D16); kind is part of the address; put is
idempotent; tamper (body or length lie) refused; absent and malformed refs
refused; `chunk` refused. Workspace: unannounced root refuses paths and
Ensure; announce prints path + source; default under $HOME; second live
process refused; stale lease taken over with epoch+1 and the old holder's
Release cannot clobber it; epoch monotonic across release. Contracts:
committed generated files do not drift from the types; every kind ships a
declared file and the pair reports no errors (warnings printed, never
silenced); reference reader forward (unknown field + unknown value) and
backward (all optionals absent) for every kind; must-detect fixtures for the
report (declared-not-generated, authorization+accepted-unchanged, defined
without pattern, design-pending) with a negative control; undeclared → warned;
generated shape is derived (thought field recognised, header flattened,
markers never on the wire).

**Divergences from the design note, recorded:** hash algorithm (SHA-256, not
blake3); declared files are JSON. Both are implementation choices inside
the "how is mine" line.

**Residuals carried to step 2:** the generated contract's `Omittable` is
derived from omitempty/pointer/slice/map only — `absence-possible:
by-construction` (contract input §9) is not yet expressible; `measured_by`
is free text, not a resolvable pointer; the drift gate ignores field ORDER
(a reorder is not a contract change, but it is not proven harmless either).
