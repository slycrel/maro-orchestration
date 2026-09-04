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

**Review round (one pass, Skeptic + Expert QA, codex; ledger and reviews in
the review dir as `step1-*.md`).** 10 + 13 findings, every cited line real.
All fixed in one round, same commit series:

- Lease: exclusivity is now an OS advisory lock (`flock`) on `lease.lock`
  held for the process lifetime — the kernel releases it on death, so
  "stale" is never inferred from PIDs. Epoch parses strictly (malformed →
  refuse, `MaxUint64` → refuse, read back after write). Malformed
  `lease.json` under a FREE lock is a dead holder's debris: taken over with
  the recovery recorded on the lease; a directory or unreadable file
  refuses. Six real processes race in a test; exactly one wins and its
  durable lease equals the returned one.
- Durability: every workspace write is unique-temp + fsync file + rename +
  fsync directory (`WriteFileDurable`).
- Announce: returns an error; only a successful write yields the
  `Announced` capability, and `thought.Open` takes ONLY that — there is no
  way to open a store on an arbitrary path.
- Thought: `Put` reads and verifies an existing body before vouching for it
  and verifies after its own write; `Ref.Validate` (known kind, exact
  prefix, 64 lowercase hex, non-negative bytes, closed encoding) runs on
  every public operation; `Has` returns `(bool, error)` so malformed is
  never "absent"; JSON round-trip across a store reopen.
- Records: kind derived from the CONCRETE registered type (pointer or
  value), a wrapper claiming another kind is refused; `Subject` required;
  canonical `<kind>/<n>` (no `01`); ULID timestamp range-checked; `Header`
  gained `ValidateWire`.
- Contracts: strict decode (unknown keys, trailing content); closed
  vocabularies validated at load; patterns compiled AND required to carry a
  `rejects` negative fixture that must fail them; `measured_by` must resolve
  to `<dir>:<TestName>` in the tree (or `not-re-runnable-here`); header
  fields declared once in `_header.declared.json` and merged into every
  kind, reported and exercised like any other; the reference reader now
  EXECUTES each declared rule (accepted-unchanged → byte-identical survival;
  rejected → the type's `ValidateWire` refuses; default → the decoded
  value; tolerated → decodes) and refuses a fixture that exercised nothing;
  drift compares canonical bytes, censuses orphans, and checks a
  `MANIFEST.json` written last; the committed pair's warning set is a
  durable expectation; the CLI prints counts on success and is tested.

**The instrument paid for itself during the fix round:** the reference
reader found that the header declared unknown IDs/schemas as `rejected`
while neither record type executed it on the wire — fixed by
`Header.ValidateWire`. That is the finding class the contracts foundation
exists to produce.

**Residuals (recorded, not blocking):** `flock` semantics on network
filesystems are not those of a local disk — the root is declared local;
contract-directory writes are repo files, not workspace state, and are not
fsynced; `Status` reports a held lock with an unreadable `lease.json` as
"held" without naming the holder; a forged `lease.json` cannot deny
admission any more (the lock decides), which retires that reviewer concern.

## Step 2 — journal, sequencer, typed readers, cursors, projector (2026-09-05)

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| One append-only log of framed, CRC-checked envelopes; torn tail discarded on open | §2 (r2-H, S2 framing) | kept |
| Sequencer = the `Submit` critical section: validation, precondition, contiguous Seq, registry-stamped population, fsync, ack | §2 | kept — a mutex, not a goroutine: same serialization, no channel plumbing until a lane needs async submission |
| Idempotency index rebuilt from the log on open | §2 | kept (in memory; a snapshot is a later Finding when the log is large) |
| Epoch stamped on every frame; stale epoch refused | §2, D12 | kept |
| Preconditions | §2 | ONE kind (`ExpectHead`) — the only precondition any step-1..5 caller needs; richer preconditions arrive with the state machines that need them |
| Three typed readers filtered by the REGISTRY's envelope | §1a, §9 (r4-4) | kept; poisoning test: a mis-stamped frame is refused at decode |
| Durable per-lane cursor; never backwards, never past head, malformed refuses | §2 | kept |
| Projector: full-rebuild generation dir, atomic `current` swap, watermark, cursor | §2 (r2-H publication) | kept; incremental append is a later Finding with a measured reason |
| B3/B4/B6 projection mappings | design §16 step 2 | **deferred to the steps that register those kinds** (3, 5) — there is no run/call/outcome kind yet; a mapping for a kind that does not exist would be a placeholder. The mapping TABLE (`contracts/VIEWS.md`) and the first real view (`thoughts.jsonl`) exist now. |
| Multi-file view generations as a directory | §2 | kept |
| Backpressure/bounded queues | §2 | nothing to bound yet: the only lane is the projector and it pulls from its cursor |

**Built.** `internal/journal` (frame, journal+sequencer, readers, cursor),
`internal/projector` (generation publish, `ThoughtsView`), `maro-go journal
status|publish`, `contracts/VIEWS.md`.

**Edge tests.** Contiguous Seq and idempotent replay; refusals (no key, empty,
stale epoch, lane-supplied Seq, invalid record, failed precondition) write
nothing; three readers see only their population and a mis-stamped frame is
refused; torn tail truncated on open with head and index rebuilt and the next
submit continuing; a corrupted byte inside a committed frame drops that frame
and everything after; kill between every 7 bytes of a 5-frame log still opens
to a contiguous prefix; cursor durable/bounded/strict; committed vs published
(nothing at the view edge before Publish; watermark = head after); a leftover
`.building` dir from a dead process never becomes `current`; a greedy
production view cannot see control rows; path-traversing view names refused;
CLI status/publish.
