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

**Review round (one pass, Skeptic + Expert QA; `step2-*.md` in the review
dir).** 12 + 13 findings, every cited line real, all fixed in one commit:

- **Lease is the only door.** `journal.Open(lease)` — the root comes from the
  lease; a released, nil, or zero-epoch lease refuses; every `Submit`
  re-checks `Live()`.
- **One strict validator** (`decodeEnvelope`) shared by recovery, scan, and
  `Decode`: non-empty tx_id, epoch > 0, records non-empty, `last_seq`
  arithmetic, contiguous record seqs, stamp = registry envelope, body seq =
  frame seq, body kind = stamp, tx_id uniqueness; payload length bounded
  (zero and > 16 MiB refused).
- **Recovery discards only a genuinely short tail** (a short read at EOF).
  Bad magic, bad checksum, or an invalid envelope with bytes after it is
  `ErrCorrupt`: refuse to open, modify nothing, name the offset. Nine forged
  valid-CRC shapes are must-detect fixtures, each checked to leave the log
  untouched.
- **Poison on write/fsync failure**: commit state is indeterminate; every
  later Submit refuses until reopen recovers the truth (partial frame
  truncated back to the known-good offset on the way).
- **Refused commands mutate nothing**: validation before any Seq is stamped;
  a valid-then-invalid command leaves the first record's Seq at zero and the
  corrected retry succeeds.
- **Scans prove coverage**: `ScanThrough(after, through)` fails with
  `ErrIncomplete` unless every frame through `through` read and validated;
  a corrupt committed frame can never become a short successful scan. The
  projector cuts every view at ONE captured head (a record committed
  mid-publish is not in that generation — tested with a gated view).
- **Cursors**: one object per lane per journal, methods locked, `Advance`
  re-reads the durable value and refuses regression against memory or disk;
  five concurrent advances under `-race` leave 5.
- **Projector commit point = the `current` swap**, with a manifest (head,
  view hashes) inside the generation. `Published` derives the head from the
  current manifest, validates every view's hash, checks the watermark copy
  against it, and refuses dangling links, loops, escapes, tampered views,
  and a watermark without a current. Nothing `current` points at is ever
  deleted; a publish at the same head repairs a lost watermark or cursor;
  staging dirs are unique per attempt; publishers serialize on the lane
  lock; view names are checked for traversal and duplicates; file handles
  are closed on every path.
- Directory entries fsynced on first open; `Close` idempotent; closed
  journals refuse scans and cursors.

**Residuals (recorded):** a scan that fails with `ErrIncomplete` does not
itself quarantine or report the corruption to an operator surface — the CLI
`journal status` shows recovery, but corruption found by a reader at runtime
is only the error the caller gets (step 9's supervisor/health line is where
that surfaces); power-loss durability of directory entries is tested by
inspection, not by fault injection; a lease's `Live()` is "we still hold the
fd", not a re-probe of the kernel lock.

## Step 3 — invocation boundary: state machine, scripted + subprocess backends, restart reconciliation (2026-09-05)

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| Invocation state machine as records: prepared → dispatched → tool effects → terminal_observed → receipt; `Reconciled` on restart | §4 (r2-I, r3-1, r4-5) | kept |
| Effect token committed before dispatch; per-effect key `derive(token, ordinal)` | §4 | kept |
| Key HANDSHAKE (backend asks for a key before acting) | §4 | **not implementable for the claude CLI** — it performs its own tool calls. The backend declares `OutwardReconcilable=false`; its effects are post-hoc evidence (`Announced=false`); a dispatched call without a terminal reconciles to `indeterminate_external_effect`. The handshake seam exists (`Sink.Effect` returns the key) for a backend that can. |
| Registered operation table (tool name → class), unknown ⇒ irreversible | §4 (r6-3), broker | kept — the broker's enforcement (refusing non-query ops during a fork) is step 8 |
| `scripted` backend | §4, §15 | kept |
| `subprocess` (claude CLI, stream-json) | §4 | kept; flag set taken from the CLI's contract, prompt on stdin, merged capture kept as a transcript thought |
| codex CLI backend | design "claude / codex" | **deferred** — one real backend is enough for every step-4..13 scenario; codex attaches through the same seam when a reviewer or a second executor needs it |
| Retries inside the shell | §4 `Attempt` | **not in v1** — `Attempt` is carried on terminal/receipt; the driver decides retries (step 5) with the reconciliation facts |
| B4 `build/calls/call-NNNNN.json` projection | shared spec | **deferred to step 5** (needs run dirs); the records carry everything B4 needs |
| Metering targets on invocations | D13 | kept as optional `{name, limit, why}` (why required) |
| Purpose vocabulary | §4 | five values; extend with a Finding |

**Built.** `internal/invoke`: six registered kinds with `ValidateWire`,
`Backend` interface + `Sink`, `Scripted`, `Subprocess` (claude) with a
stream-json parser that reconstructs tool events from `assistant.tool_use` /
`user.tool_result` pairs and treats frames after the result that do not
parse as `partial`, the `Shell` (Invoke, Fold, Reconcile), the operation
table. Declared contracts for all six kinds; registry now 8 kinds.

**Edge tests.** Every transition committed and folded, effect classes/keys/
order, request and response stored whole, target why required, bad purpose
refused with nothing written; before-dispatch failure leaves a terminal,
failed stream leaves no receipt, partial stream leaves a receipt;
reconciliation: tool-less ⇒ abandoned, outward ⇒ indeterminate even with zero
effects, prepared-only is not an orphan, idempotent; a hung backend cut by
the context still records its terminal (bookkeeping is detached from the
caller's deadline — found by the test); parser against the CLI's event
shapes incl. error-result and unanswered tool_use; CLI args per request
shape; a fake `claude` script exercises the real subprocess path end to end
(effects, transcript kept, mid-stream death ⇒ failed with error evidence);
live smoke gated behind `MARO_GO_LIVE=1`.

**Live smoke (run once, 2026-09-05):** real claude CLI, tool-less judge
call, `PONG` back whole; receipt usage `{"input_tokens":10,"output_tokens":49,
"cost_usd":0.032923,"wall_ms":2702}` — the first real receipt in the
successor's journal.

**Review round (one pass, Skeptic + Expert QA; `step3-*.md`).** 13 + 13
findings, every cited line real, all fixed in one commit:

- **Backend contract at the boundary:** nil result, empty terminal, complete
  without a response, a panic, negative/non-finite usage, cost without
  `cost_reported` — each becomes `terminal=failed` with a contract reason
  and `ErrBackendContract`; never a panic, never an orphan.
- **Validation before writes:** purpose, target why, backend name, context,
  and `MaxInputBytes` (typed `Incapable{Actual, Max}` refusal — D16 routing
  is now enforced, not decorative) all run before the request thought is
  stored.
- **Effects as the stream reports them:** `tool_effect` is committed at
  tool_use time; `tool_effect_result` (new kind) when the result arrives; an
  unanswered use is observed-outcome-unknown, never "error". Sink
  serialized; ordinals contiguous under 25 concurrent reporters.
- **Response before terminal:** response and transcript are stored first and
  the terminal carries their refs and usage, so the receipt is derivable —
  `Reconcile` finalizes a lost receipt on restart. A response-store failure
  makes the terminal `failed` with the reason; it can never say complete
  without its bytes.
- **Outcome always carries the invocation id**, with `Err` for bookkeeping
  failures after dispatch; the "recorded terminal" claim is narrowed to
  "attempted on a detached context" — a dead lease or poisoned journal
  leaves an orphan Reconcile disposes of (tested: lease released from inside
  the backend).
- **Fold is a validating automaton:** duplicates, transitions out of order,
  effects whose key or class do not derive, results for unobserved effects,
  receipts without or disagreeing with terminals, reconciliation after a
  terminal, dispositions contradicting the evidence — ten forged histories,
  each `ErrFoldCorrupt`.
- **Disposition is one rule:** abandoned only when the backend cannot act
  outward AND no observed effect could have; evidence dominates the
  capability snapshot.
- **Parser is a protocol state machine:** result closes it; frames after
  it, a duplicate result (⇒ failed), unmatched tool_results, empty tool
  names, undecodable JSON-looking frames are violations (⇒ partial);
  subtype≠success is failed regardless of is_error; cost absence is
  `cost_reported=false`, never zero-means-free.
- **Scanner overflow** cancels and drains the child and names the cause;
  capture and transcript-store errors land in the terminal reason; zero
  timeout defaults to 20 minutes.
- **Evidence is byte-preserving** (base64 with role); invalid JSON input and
  non-UTF-8 output round-trip exactly.
- **Kill-site matrix through the real Invoke** via a crash seam
  (`Shell.CrashAt`): after prepared / dispatched (tool-less and outward) /
  effects (tool-less snapshot + Bash effect ⇒ indeterminate) / terminal
  (⇒ finalized receipt); each reopened, folded, reconciled, and idempotent.
- Fake-CLI battery extended: subtype error, trailing frames ⇒ partial, a
  70 MB line ⇒ failed without deadlock, stderr-only ⇒ failed with the
  transcript kept, a 3 MiB prompt delivered whole on stdin.

**Residuals:** the crash seam stops the Go code, it does not kill the OS
process mid-syscall (the journal's own kill tests cover torn frames);
`Reconcile` finalizes but does not retry abandoned invocations (the driver's
job, step 5); the broker does not yet refuse operation classes (step 8).

## Step 3½ — contracts foundation, v5 fold (2026-09-05)

Jeremy updated the work-side practice (v5, "promotion fold"); distilled in
`contract-testing-input.md` §18–§26 and applied:

- `format_version` on every generated and declared file (absent reads as the
  founding revision); the answer key carries `since` per key.
- Declared files are read TOLERANTLY: an unknown key at any nesting is a
  vocabulary WARNING naming key and path, never a refusal (corrects step 1's
  strict decode); `x-` keys are legal, kept verbatim, and must have a row in
  the file's `improvised` register (a bare one is an error). The committed
  pair pins zero unknown keys and zero unregistered improvised keys — the
  guard the practice says discipline cannot replace. Generated files stay
  strict (machine-written: an unknown key there is a hand edit).
- Regeneration diffs are CLASSIFIED: added field / bumped schema → additive;
  removed or renamed field, retype, presence-class flip, envelope change →
  BREAKING, named as such by `contracts check` so it cannot land as "no
  drift" (the standard's rule 2; D3's lifecycle is the path).
- `contracts report` ends with the six-item "what this run could not settle"
  block: pair diff and class; warnings; errors; design flags; insufficiency
  (improvised keys, undefined dimensions); deliverable class.

Not applied (recorded): the rename window (alias → deprecated → removed,
≥ 90 days) has no instrument yet — nothing has been renamed; `Change` is the
place it plugs in. Vendored-pair reading under an older answer-key SHA is a
consumer concern that arrives with the pack (step 12).
