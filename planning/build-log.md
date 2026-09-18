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

## Step 4 — observations, verdicts, resolver (2026-09-05)

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| `Observation` (deterministic checks; refuted / supported / could_not_observe) | §6 (r2-L) | kept; five check kinds registered, the checks themselves arrive with the judges in step 7 |
| `Verdict` with closed per-kind outcome vocabularies, standing, direction fixed by standing, falsifiers on closure | §6, §8.2 | kept |
| `Resolution` as a journaled record naming every candidate, the rule, and the resolver version | §6 (r2-L, r3 partial order) | kept |
| Resolver as a pure fold over the SET (sorted by id, never Seq); supersession as the only use of journal linkage | §6 | kept; permutation test over every arrival order |
| Refutation threshold | D13 (numbers carry a Why, reported not enforced) | kept as `Thresholds{Refute, RefuteWhy}`; v1 registration 0.9 with its why; re-measure once live |
| Sheriff | §6 | **step 7** (needs the supervisor) |
| Judges that PRODUCE verdicts (closure judge, provenance) | §5/§7 | **step 7** (need the AGENDA driver); step 4 is the vocabulary and the fold |
| Confidence combination across candidates | reviewer suggestion r2 | **not in v1** — rank, then confidence, incomparable ⇒ contested; combining confidences is a modelling choice with no measured basis yet |

**Built.** `internal/verdict`: three kinds with `ValidateWire`; `Resolve`
(supersession → refutation → standing/confidence → self-cannot-promote);
`Fold` grouping by (subject, kind); `Commit` idempotent per candidate set.
Declared contracts for all three; registry at 12 kinds.

**Edge tests.** Every permutation of three candidate sets (with observations
reversed) yields an identical resolution; operator outranks a more confident
judge; within a rank confidence decides; equal rank + confidence + different
outcomes ⇒ contested with no effective verdict; agreeing peers are not
contested; a self success claim resolves to the kind's unknown while a self
demotion stands and a judge establishes success over it; supersession drops
the named verdict but keeps it as a candidate; a refuting observation at the
threshold settles failure without a judge, weak/unobservable/supporting ones
do not, an operator outranks the check, kinds without a failure outcome
ignore observations; closed vocabularies, direction fixed by standing, judge
needs an invocation, confidence in [0,1], falsifiers closure-only, contested
never has an effective verdict, foreign resolver version refused; journaled
resolutions fold and are idempotent per candidate set.

**Review round (Skeptic + Expert QA, codex; 22 findings, verified in the
tree before fixing).** Every HIGH was real; the fixes:

- *Wire rules never executed* (both lenses, HIGH). `record.Validate` now
  calls every registered type's `ValidateWire` — so the declared
  `rejected` rows are executed at both journal doors (submit and recovery)
  for all 12 kinds, not documented. Cross-cutting: two fixtures in other
  packages were wire-invalid and had been passing; fixed. Journal-level
  must-detect added (`TestJournalExecutesDeclaredVocabulary`); mutation
  (disable the call) kills it and the verdict twin.
- *Resolve trusted unvalidated inputs* (HIGH). `Resolve` returns an error;
  candidates are validated first (subject present and uniform, kind uniform,
  wire-valid, NaN confidence refused, Seq present).
- *Supersession as an unchecked tombstone* (both, HIGH). The graph is
  checked: target must be a candidate, the replacement's Seq strictly later,
  standing never lower. Bad links refuse the whole set (a lane wrote an
  invalid verdict — that is the defect to surface, not to route around).
- *Observations applied to unrelated kinds/claims* (both, HIGH). An
  applicability table (check kind → verdict kinds) attaches observations;
  refutation names ALL decisive observations (≥ threshold) in `Decisive`,
  not the first found; an operator verdict still outranks the checks.
- *Current resolution unwired; overlapping commits* (both). `Current` folds
  journaled resolutions to the inclusion-maximal one per (subject, kind);
  incomparable candidate sets are an error, not a silent pick.
- *Idempotency key* (both, HIGH). Structured key = sha256 of canonical JSON
  {resolver version, subject, kind, thresholds, candidate IDs, observation
  IDs}; a replay returns the COMMITTED resolution (read back), never a
  recomputed one, plus `ErrAlreadyResolved`.
- *Ties broken by ID / NaN* (both). Agreed maxima with different outcomes ⇒
  contested with `Effective` empty; `Candidates`/`Decisive` are sorted for
  canonical output only, never consulted for choice. Test covers Seq
  renumbering and observation permutations.
- *Degenerate kinds asserted failure* (QA MEDIUM). Kinds without a failure
  outcome (`stuck`, `delivery`) resolve to their unknown; `Resolution`'s
  validator refuses rule/shape contradictions (effective ∉ candidates,
  contested with an effective, refuted without decisive).
- *Threshold boundary untested; thresholds unvalidated* (both). Thresholds
  validated at registration; boundary at exactly `Refute` exercised.

Test count in `internal/verdict` 6 → 10; all with `-race`. Deferred, not
lost: combining confidences across agreeing candidates (no basis yet);
the reviewer's "sufficient set" reading of refutation stays a set-of-
decisive record, threshold on the max — revisit when the sheriff (step 7)
has live numbers.

## Step 5 — run state machine, driver, intake, NOW, delivery outbox (2026-09-05)

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| `Goal` (text as a whole thought; origin; `DeliveryPolicy`), `FamilyAssessment` (treatment-blind, rule-versioned), `RunAttempt` (config snapshot, recovers_from) | §3, §5 intake, §8a population | kept |
| Run state machine as `Transition` records: created → executing → judged → recorded → delivered{transport_accepted, user_acknowledged} / delivery_failed; `recoverable` on restart | §5a | kept; legal-edge table executed at the journal door |
| Execution outcome as a fold stamped on `recorded` | §5a "both folds" | kept; a test recomputes it from receipt + resolution alone |
| Mission outcome = execution ⊗ delivery under policy | §5a, §8 item 1 | kept as a pure fold (`MissionOf`), never a record — derivable |
| Driver with pure stages (Intake, SelfVerdict, Render) and one shell; lifecycle event per boundary with handle/run/goal/attempt | §5, FINDINGS #9 | kept; events are a view, transitions the durable form |
| NOW = driver configuration (plan cardinality 1, judge `self_only`) | §5 | kept; `agenda` and the model judge are ADDITIVE vocabulary arrivals in step 7, not enumerated now (L28: no wrong-at-birth enumerations) |
| Delivery outbox: `DeliveryPrepared` (payload stored first; nonce), `DeliveryAttempted` (bounded), `DeliveryAcked` (client token bound to delivery + payload hash) | §12 | kept; bound = 3 with its why in the code |
| `endpoint_accepted` | §12 | **not in the vocabulary** — no v1 producer (needs a program origin) |
| `Interrupt` records | §5 | **step 7** — meaningful at stage boundaries of a multi-step plan; a NOW run is one execute call |
| Model judge / deterministic observations in NOW | §6 | **step 7** — NOW v1 resolves the self claim honestly to `unknown` (self cannot promote); the ledger row is UNJUDGED, which is B6's tri-state exactly |
| Always-on submission via the lease's socket | §10 | **step 7** (supervisor); v1 `maro-go now` runs the driver in-process under the lease |
| B6 outcomes view (`outcomes.jsonl`) | §13 shared edge (owed since step 2) | kept, EXACT to B6: required keys, A6 absent-not-null verdict, `now_self_verdict`/`closure` source vocabulary; mapping row in `contracts/VIEWS.md` |
| Stale-ack refusal | §12 acceptance list | kept as a guard but **unreachable in v1** (one delivery per run; it exists for the re-deliver verb) — named, not claimed tested |

**Built.** `internal/run`: 7 kinds (registry at 19), classifier `family/1`,
`Fold` (per-run ledger, refuses illegal histories), `Driver.Run`/`Resume`
(reconcile → recover → deliver; every commit keyed), outbox `drain`, `Ack`,
`CLIOrigin`, `OutcomesView`. CLI: `now [--backend] [--model] [--ack]`,
`ack`, `runs [resume]`; `journal publish` now writes both views.

**Edge tests (10, all `-race`).** Full NOW run: state trail, events, mission
fold, exact B6 keys with `goal_achieved` ABSENT; failed execution delivered
honestly = `mission_failed(execution)` with a judged `not_achieved` self row;
ack protocol (wrong token, wrong-payload token, unknown delivery, replay,
ack-before-presentation); **kill matrix** of 13 seams (intake, start,
executing, invoke prepared/dispatched×2 caps/terminal, execute, judged,
recorded, prepared, present, attempted) each resumed to exactly one
delivered run, one goal, one recorded outcome, the expected dispatch count
(indeterminate ⇒ 0 replays), the expected presentation count (crash after
present ⇒ presented twice, honestly), and a no-op second resume; bounded
outbox → `delivery_failed` + no retry on resume + no ack; classifier
determinism; 21 journal-door refusals; fold refuses an orphan transition;
recorded outcome equals its recompute.

**Live.** `MARO_GO_LIVE=1` test: haiku PONG delivered, cost 0.036. CLI end to
end on a scratch workspace: `now --ack` printed the answer and the ack
command; `runs` showed `accepted_unacknowledged`; a wrong token was refused;
the real token acknowledged; a repeat replayed; `runs` then showed
`delivered` with `user_acknowledged`; `journal publish` wrote the B6 row.
Side-find from the live row: `model` was empty — the outcome fold now
carries it.

**Residuals.** Ack token = sha256(delivery, payload hash, nonce) with the
nonce in the journal: proof of presentation for a same-box CLI client, not a
secret against a journal reader — a remote origin needs an HMAC secret
outside the journal. `recorded_at` format is RFC3339 micro-Z; B6 does not
pin one. `Outcome.ClosureSrc` is read from the single candidate (step 7 reads
the effective verdict's standing from the resolution).

**Review round (Skeptic + Expert QA, codex; 23 findings, deduplicated to 14,
each verified in the tree before fixing).** Every HIGH was real:

- *Pre-dispatch refusal looped forever* (both, HIGH). A `MaxInputBytes`
  refusal is made by the shell before anything is written, so the attempt
  died at `executing`, Resume marked it recoverable, and attempt N+1 hit the
  same refusal, forever. Now: `invoke.Incapable` is a recorded honest
  failure (`backend_incapable`, no invocation, no provenance) and Resume is
  a no-op after it. Plus a general recovery bound (`MaxAttempts` 3, why in
  the code): past it the next attempt records the loop as its failure.
- *Reused receipt attributed to the resuming backend* (both, HIGH).
  `Outcome.Model` came from the recovering attempt's config. Now provenance
  is the producing invocation's (`Produced` attempt + its `Backend.Model`),
  and the fold refuses a recorded model that disagrees with the invocation.
  Test resumes under a different model and checks the row.
- *Forged acks and cross-scope delivery records folded* (both, HIGH). The
  door checked shape only; the fold attached attempts/acks by delivery id.
  Now `checkAck` is ONE rule shared by the writer (`Ack`) and the reader
  (`Fold`): a start must exist, the token must be the bound one, the hash
  the payload's, the scope the owner's; attempts and starts must be owned,
  in order, and never after acceptance; a prepared delivery must match the
  goal's origin and policy. Ten forged-history fixtures, each wire-valid
  record by record, each refused with its reason.
- *Transitions claimed delivery without evidence* (both, HIGH). `delivered:
  transport_accepted` needs an accepted presentation; `user_acknowledged`
  needs the ack; delivered→delivered may only promote transport→user;
  `delivery_failed` needs exhausted, all-failed presentations. The fold
  executes these; `transition()` is the driver's one writer.
- *Recorded outcome could lie* (QA HIGH). The fold now checks the outcome
  against the run: goal thought, invocation/receipt/response/usage against
  the invocation state, closure against the resolution (outcome, confidence,
  effective standing), and — new instrument — `verdict.Check` RECOMPUTES
  every journaled resolution from its named candidates and refuses any
  disagreement. Fixtures: other goal, other model, other usage, promoted
  closure, invented source, foreign closure, a resolution that does not
  re-derive.
- *Negative delivery bound panicked* (both). Bounds < 0 are `ErrConfig`;
  the exhausted branch no longer indexes an empty slice.
- *Presentation not atomic; duplicates invisible; ack race* (both, MEDIUM).
  New kind `delivery_started`, committed BEFORE the outward write (the same
  shape as a ToolEffect before its action). On restart a start without a
  result is resolved `unknown` ("the user may have seen it"), the next
  presentation says so out loud, and the mission fold carries
  `MayDuplicate`. An ack is valid from the start on — the token only
  reaches a client through a presentation — so the ack race is gone: the
  crash-after-display test acks from the first display and is NOT presented
  again. Origin panics are contained as failed presentations. The CLI origin
  composes once and writes once (short write = failed presentation).
- *`lane` is not a B6 key* (both). Removed; `task_type` carries it. The row
  was then read through the Python readers themselves on a scratch
  `MARO_WORKSPACE` (never the live one): `load_outcomes` → `Outcome` with
  `goal_achieved None`, `verdict_trust` → `neutral`; a judged self row →
  `full`. Recorded here as a probe, not a test (the Go suite does not run
  Python).
- *Second assessment overwrote the first* (Skeptic). The fold refuses a
  second `FamilyAssessment` for a goal; Resume reads the fold's map.
- *Empty goal accepted by the driver* (both). `ErrEmptyGoal` before
  anything is stored. *Ack while `now` holds the lease* (both): a specific
  message; the token stays valid.

Not done, recorded: a presentation-intent record for REMOTE origins needs
an HMAC secret outside the journal (same-box CLI is fine with the nonce);
`endpoint_accepted` still has no producer. Tightening `run_transition/1`
(produced_by) and `delivery_attempted/1` (`unknown`) happened inside this
unlanded step, so no version bump; the pre-fix scratch journal is refused
by the new rule, which is the rule working. Test count in `internal/run`
10 → 16 (+ 7 forged-history subtests, + 15 kill seams); registry at 20
kinds; contracts report 0 errors / 0 warnings.

## Step 6 — memory: learned revisions, lifecycle standing, one recall query, applications, re-run identity (2026-09-05)

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| `LearnedRevision` (item id minted once; predecessor chain; kind lesson\|policy; scope; family; text as a whole `lesson_text` thought; provenance) | §7 | kept |
| `LifecycleTransition` per EXACT revision; the 8-stage vocabulary and the legal-edge table | §7 (single authoritative vocabulary) | kept; executed at the door (edge legality) and the fold (from-stage, evidence precedence) |
| Transition actors | §7 | `operator` only — the one producer that exists (CLI restamp); `tenure` (step 9) and `measurement` (steps 10–11) are added with their producers |
| Two folds kept apart (standing per ItemRev; current revision per item) | §7 | kept; a new revision starts at candidate whatever its predecessor earned |
| `Recall(purpose, scope, standing) → RecallSelection` — one query | §7 | kept; item-id order; exclusions as counts by reason + top-5 (§14) |
| Projected size | §7, D13 | reported, never a cap — no truncation anywhere on the block or the request |
| `Application` proving a revision reached a request (representation = exact bytes in the request thought) | §7, §8a exposure | kept; committed after the invocation exists; re-derived on recovery from the committed selection |
| `PolicyApplication`, `PolicySelection`, the policy apply surface | §7, D17 | **step 10** — `policy` revisions can exist (vocabulary is the design's) and recall never injects them (tested) |
| Re-run identity | §16 step 6 | `ReplayKey` over committed evidence only: goal thought, config snapshot, included ItemRevs, request thought, terminal |
| Scope chain own → parents → root → workspace | §3 | kept and walked; v1 goals are roots, so the chain is [goal, workspace] until fork (step 8) |
| B7 lesson-store projection (`memory/lessons.jsonl`) | §13 shared edge | **step 12** with the import — the Python readers of B7 need the whole store semantics, not a row per revision |

**Built.** `internal/learn` (4 kinds; registry at 24; contracts 0/0):
records, `Fold`, `Recall`, `Render`. Driver: recall committed before every
execute invocation, applications after it, `Outcome.Recall`; recovery
re-derives applications from the recovered attempt's selection; the run
fold refuses a recorded outcome whose applications are not exactly the
selection's included set. CLI `learn add|stage|list`.

**Edge tests.** learn: standing is per revision (predecessor's `effective`
does not select a `candidate` successor; quarantine on the old one stays
there); recall determinism, scope/family/kind/stage exclusions with
reasons, bounded top-k, projected size, render contains every
representation; 5 door refusals + 7 fold refusals. run: the design's
end-to-end — candidate absent from the request, promoted present as an
Application whose bytes are in the request thought, quarantined absent
again with the request hash back to the candidate run's; a forged extra
application refused by the fold; replay keys equal across identical
re-runs and different after a quarantine; kill matrix now carries a
promoted lesson on every seam plus `after_recall` and
`after_applications`, each resumed with the application intact.

**Live.** `learn add --family answer "…reply in UPPERCASE…"` → `learn
stage provisional` → `now --model haiku "chemical symbol for gold"` →
recall 1 of 1, applied 1, answer `AU`. The lesson reached the request and
the answer followed it: the first measured-by-eye behavior change from
learned data (D11 is still owed its measurement, steps 10–11).

**Review round (Skeptic + Expert QA, codex; 24 findings, deduplicated to 15,
each verified in the tree before fixing).** The HIGHs were all real and all
of one family — derived records trusted on identity alone:

- *Applications proved the right revision with the wrong bytes* (both,
  HIGH) and *the request was never checked against the selection*. The run
  fold now RE-DERIVES exposure from committed evidence: the goal thought
  plus the selection's deterministic rendering must be byte-equal to the
  invocation's request thought (`thought.Address`, no substring check), and
  every application must be that rendering's bullet for its item, scoped to
  the producing attempt. Fixture: a forged representation on the right
  revision is refused at `recorded`, and Resume refuses to repair around it
  (`ErrIntegrity`) instead of appending.
- *Forged RecallSelection injected anything* (both, HIGH). A selection is a
  DERIVED record: the learn fold recomputes `Recall` over the ledger as it
  stood at that Seq and refuses any field that differs; execute recalls must
  use exactly the selectable set. Fixtures: old effective revision, candidate
  current, policy, wrong standing, false projection, a continuation that
  differs.
- *`apply` compared counts* (both, HIGH). Exact ordered compare of item,
  revision, and representation address; a mismatch is journal evidence the
  driver could not have written, refused as such.
- *Two extra lifecycle edges* (both): candidate→contested and
  observed→contested let unmeasured data become selectable. Removed; the
  whole 8×8 matrix is now tested against the design's prose in both
  directions.
- *Self-evidence passed* (Skeptic). A record is "seen" only after its own
  checks. (The rule has no producer until step 9's actors; kept, not
  claimed tested end to end.)
- *A pre-dispatch refusal dropped its recall; ReplayKey mixed outcome into
  identity* (both). The refusal outcome names its selection; the fold
  requires a refusal's recall to be its own attempt's; the replay key is
  inputs only (terminal removed) and carries the selection whenever the
  outcome names one.
- *Crash after recall re-decided over a moved ledger* (Skeptic MEDIUM). One
  rule now: a recovered attempt that committed its selection and never
  invoked CONTINUES it (`RecallSelection.Continues`, checked for equality
  with what it continues); tested with a quarantine landing between crash
  and resume — the continued lesson is still rendered and applied.
- *Whitespace why; operator evidence that looks authoritative* (both).
  Trimmed checks; an operator transition carries a why and NO evidence.
- *Goal-scoped lessons to nonexistent goals* (Skeptic): the fold requires an
  earlier goal record. *Family "none"* reserved at the door.
- *Multiline lessons forged bullets/headings* (both). Continuation lines are
  indented under the bullet (`Frame`); bullet count equals included count;
  bytes otherwise untouched.
- *"Top"-k was a sample* (both): renamed `excluded_sample`, defined as the
  first K excluded in item order. *Determinism test proved sorting* (both):
  now two journals, same records, different arrival orders, byte-equal
  render and field-equal selection, sample identities and reasons asserted.
- *CLI stage race* (both): `learn stage` submits with `ExpectHead` = the
  head its fold read, so a concurrent transition is refused unwritten.
- *No subprocess assertion* (QA): a fake CLI captures its stdin, which must
  equal the request thought (goal + block) byte for byte.

Tests: learn 3 → 7; run 21 → 26 (+ forged-exposure, refusal-recall,
continuation, scope/policy, subprocess). Contracts 0 errors / 0 warnings.
Not done, recorded: byte-level exposure needs the thought store, so
`run.Fold` now takes it (every caller has one); an evidence vocabulary for
non-operator transitions arrives with their producers.

## Step 7a — supervisor + lanes, Sheriff, AGENDA configuration (2026-09-05)

Step 7 is landed as two chunks (fan-out decree 2026-07-31: honest slices with
upgrade edges = chunk points): **7a** = supervisor, generic lane lifecycle,
Sheriff, AGENDA driver configuration, in-process; **7b** = the always-on
process (socket intake, `submit`, interrupts, the supervisor wired into
`serve`).

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| `Supervisor`: lane registration, per-lane goroutine with panic capture, bounded restart (why in code), heartbeat + progress watermark, stall detection, quiesce by stage, `Health` line | §10, §2, D12 | kept |
| `lane_event`, `lane_heartbeat` records | §10 | kept, CONTROL envelope (about the process, never a run; production readers never see them); retention bounded |
| Sheriff as a supervised lane emitting `stuck` verdicts that name their evidence; thresholds config with a why, reported never enforced | §6, §10 | kept; one verdict per (run, attempt), resolved; `runs` prints STUCK; nothing is killed |
| AGENDA = driver configuration: Intent → Plan → per-step execute + judge → closure judge | §5 | kept; `JudgeSelection model`, `Lane agenda` added additively as planned in step 5 |
| Interpretation boundaries validated once (`ParseIntent/Plan/Judge`; strict JSON; typed `ErrBoundary`) | §1b, §13 | kept; a refused judge output = `unjudged`, continue; a refused plan = recorded failure naming the boundary; a refused closure = self claim alone |
| Plan cardinality | §5 | uncapped — what the model produced (D13/D15: no chopping) |
| Deliverable = every step's whole result in order; unexecuted steps named; the question when unclear | D16, §8.1 | kept |
| Recovery mid-plan: inherit committed stages, reuse in-flight invocations with receipts, continue the recall selection | §5a | kept; fold inherits the same way and executes stage order and citations |
| Interrupts at stage boundaries | §5, behavior FINDINGS #1 | **7b** — an in-process CLI run cannot receive one; they ride the socket |
| Always-on submission (`serve`/`submit`) | §10 | **7b** |
| Routing NOW vs AGENDA by classifier | vision "routes it" | **not in v1** — explicit verbs (`now`, `agenda`); the director-clarification memory's ask-first stays a later arc |
| Clarification loop (unclear goal → ask → continue) | §5 Intent | **not in v1** — an unclear goal delivers its question as an honest failed execution; a conversation loop is a later Finding |

**Built.** `internal/supervise` (2 control kinds), `internal/sheriff`, run
AGENDA (3 kinds; registry at 29; contracts 0/0): `Driver.Lane/Judge/Health`,
`agenda_driver.go`, lane-aware fold (usage as the goal's total cost, per-
invocation exposure with a byte-suffix rule for templated prompts, stage
order/citation rules), `Outcome.Lane/Steps`, B6 `task_type` = lane. CLI
`agenda [--model] [--judge-model]`. Side-fix: `workspace.Lease` mutex (Live
vs Release raced at shutdown under `-race`).

**Edge tests.** supervise: panic contained, restart bounded, gave-up in
the health line, control-envelope isolation; stall reported not enforced,
heartbeats once per watermark move; quiesce order intake → sheriff →
executor → delivery, failure restart, a non-quiescing lane named. sheriff:
stuck once with the last evidence as basis, idempotent across ticks, never
for delivered runs, mission carries it; supervised lifecycle. run: the
behavior suite's agenda-happy-path (7 invocations, judge falsifiers as
thoughts, step prompt carries plan + prior results whole); blocked step
honest (later steps not run, deliverable names them, row judged
not_achieved); unclear goal delivers the question; boundaries refuse
malformed outputs (empty plan, non-JSON judge, confidence 7, unknown
fields, trailing content); kill matrix over after_intent / after_plan /
after_step_execute / after_step / invoke:terminal — each resumed to one
delivered run with exact executor and judge call counts; door + fold
vocabulary (second plan, step after blocked, plan citing a non-plan call).

**Live.** `maro-go agenda --model haiku "Name the three largest moons of
Jupiter by diameter, then state which is larger than Mercury"`: intent
clear → 6-step plan → 6 steps each judged done → closure `achieved` 0.98
(judge) → delivered; B6 row `task_type agenda`, `goal_achieved true`,
source `closure`, cost 0.40 over 14 calls. The first judged-achieved run
of the successor.

**Review round (Skeptic + Expert QA, codex; 31 findings, deduplicated to 17,
each verified in the tree before fixing).** The HIGHs were real:

- *Usage double-counted on recovery* (both, HIGH; masked by zero scripted
  usage). `reuse()` added a receipt the earlier-attempts sum already held.
  Now one accumulator: earlier attempts' receipts + this attempt's new
  calls. Test: every scripted call carries distinct non-zero usage and every
  seam (10) resumes to an outcome equal to the fold's sum of receipts.
- *Judge invocations and verdicts never reused* (both, HIGH). A crash after
  a step judge's receipt (or verdict), or after the closure judge's receipt
  (or verdict), re-asked the model and orphaned the committed evidence —
  and a replayed idempotency key returned the OLD record while the driver
  cited a NEW id. Now the recovered attempt reuses the in-flight judge
  invocation by ordinal and inherits a committed step/closure verdict
  (`priorVerdict`); seams `after_step_judge`, `after_step_verdict`,
  `after_closure_invoke`, `after_closure_verdict`, `after_judged` are in
  the matrix with exact executor and judge call counts.
- *`failed()` mislabelled provenance* (Skeptic HIGH): the judge model and
  the recovering attempt were stamped on executor and reused invocations.
  Now provenance is the invocation's (this attempt's `made` map, or the
  fold's).
- *Stage records trusted without their responses* (both, HIGH). The fold
  now RE-EXECUTES every interpretation boundary on read: an
  IntentAssessment must re-parse from its cited response and its request
  must be the intent prompt over the goal; a Plan's step thoughts must be
  the addresses of the re-parsed steps; a StepDone's invocation must have
  been asked exactly step k's prompt (goal + plan + prior results + recall
  block, re-rendered) with this result and terminal; a judge-standing
  verdict must cite a judge call whose request is the judgement's prompt
  and whose response parses to its outcome and confidence, with the
  receipt as basis. Fixtures: an intent contradicting its response, step 2
  citing step 1's invocation (refused: "not asked step 2's prompt"), an
  honest unjudged step (accepted).
- *Forged stuck resolutions silenced the sheriff* (both, HIGH). Every
  Resolution now passes `verdict.Check` at scan time; an attempt is
  "called stuck" only when the effective verdict is deterministic or
  operator standing with a basis. A self stuck opinion is evidence, not
  the sheriff's call.
- *Partial executor streams promoted to complete* (both). `StepDone.Terminal`
  (complete|partial, equal to the invocation's); the judges are TOLD which
  results ended partial; the execution outcome is partial if any step was.
- *Zero-confidence judge promoted* (QA HIGH, Skeptic LOW). Resolver rule 5:
  a success claim below `Thresholds.Promote` (0.5, why in code) by anyone
  but an operator is an abstention → unknown; demotions stand at any
  confidence; the boundary promotes. Registered in the resolution contract.
- *Sheriff ignored stage records* (both). The fold tracks each attempt's
  newest committed record (by journal order; time = latest At seen); the
  sheriff's basis names it. Test: newest evidence a StepDone.
- *Supervisor semantics* (both): a lane returning nil while live is a
  failure (bounded restart), not "stopped"; `Start` validates (negative
  bounds refused, 0 = default, a supervisor not made by `New` refused);
  `Stop` sets a stopping flag under the mutex (no relaunch once stopping)
  and re-reads the lane after every wait so a generation that replaced a
  finished one is awaited; a refused control record is the health line's
  first item; a cancelled lane's heartbeat is liveness only. Side-find
  while fixing: the restart path closed the NEW generation's done channel
  (close of closed channel) — each generation now closes its own.
- *Pre-dispatch refusal inside AGENDA* (both MEDIUM on uncapped plans): an
  `Incapable` from a step whose composed prompt is over the backend's
  maximum is a recorded honest failure of that step, delivered with the
  done steps named. Plan cardinality stays uncapped (D13/D15); the
  quadratic prompt growth is a STATED consequence of whole results (D16),
  to be routed by reference when a backend that reads by reference is the
  executor — not chopped.

Tests: run +4, supervise +1, sheriff +1, verdict +1 (13 packages green
under `-race`; contracts 0/0). Not done, recorded: the shutdown DAG beyond
lanes (sequencer freeze, projector, journal close) is 7b's `serve`; the
CLI path supervises nothing until 7b wires the supervisor into the
process; an early-failed step's receipt output is not in the deliverable.

## Step 7b — the always-on process: socket intake, executor lane, interrupts, quiesce (2026-09-05)

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| `maro-go serve`: one lease, one supervisor, lanes intake (1) / sheriff (2) / executor (3) / publisher (4); SIGINT/SIGTERM → quiesce in stage order → final publish → journal close → lease release | §10, §2, D12 | kept |
| Submission = the CLI writing a Goal into the running process's journal through its socket | §10 | kept: Unix socket `<workspace>/maro.sock`, one JSON request line, JSON event lines back; the presentation goes to the submitting connection (origin `socket`) |
| `Goal.Lane` (explicit routing recorded on the goal; the attempt's config must equal it) | §5 "routes it", D10 (the edge is the record) | kept; the driver reads the goal's lane, `Driver.Lane` is only the in-process verbs' default |
| Interrupts consumed at stage boundaries, acknowledged, expired when the target is terminal | §5, behavior-suite FINDINGS #1 (make interrupt intake a registered contract) | kept: `interrupt` / `interrupt_ack` records; boundaries `before_execute` (NOW), `before_step_k` (AGENDA); an interrupt for a terminal run is acknowledged `expired` in the same command |
| One heavy job at a time | D6 | kept: the executor is one goroutine of `Resume` loops, woken by intake or a poll |
| A client that is gone | §12 | a failed presentation → the outbox's bound → `delivery_failed` with the reason; the payload stays in the store (a re-deliver verb is a later Finding) |
| Backpressure via durable lane cursors | §2 | **not yet** — lanes fold the journal per pass (fine at this scale); cursors when a lane's pass is no longer the whole journal |
| Sequencer freeze / final watermark as explicit records | §2 | **not yet** — the journal's close after the lanes quiesce is the freeze; a `final watermark` record when a second front end attaches |
| Socket auth | §10 | **not in v1** — the socket lives in the workspace; the file mode is the boundary |

**Built.** `internal/process` (`Serve`, `Stop`, four lanes, the socket
protocol, the socket origin, `Dial`/`Submit`/`One`); run: `Goal.Lane`,
origin `socket`, `Interrupt`/`InterruptAck` (31 kinds; contracts 0/0),
`Driver.interrupted`, `finish()` split out of `drive()`. CLI: `serve`,
`submit`, `interrupt`, `status`; `ack` goes through the socket when a
process is up. Side-fix from the live run: the listener closed before the
intake lane was cancelled, so its quiesce read as a failure — now
cancellation closes the listener.

**Edge tests.** submit → presentation on the submitting connection → done;
ack over the socket (bad token refused, replay); two goals in sequence;
status (4 lanes, missions, no degraded line); socket-origin runs in the
journal; every lane's events end `started stopped` after Stop; the socket
refuses after Stop. An AGENDA run gated between steps: interrupt lands
while step 1 is in flight → consumed at `before_step_2`, step 2 never
runs, the deliverable carries step 1, names step 2 unexecuted and the
reason; a second interrupt on the terminal run expires; the fold holds
both with their acks. Stop mid-run → Serve again → the orphaned run is
resumed; with no client its presentation fails within the bound →
`delivery_failed` ("no client"); a new goal on the new process delivers.
Door rules for interrupts and the goal lane (6 refusals).

**Live.** `serve --model haiku` → `submit` (now): delivered; `submit
--lane agenda --ack`: a 5-step plan, closure achieved, `accepted_
unacknowledged` → `ack` through the socket → `user_acknowledged`;
`status` shows 4 lanes up and both runs; `interrupt` on a terminal run
→ expired; SIGTERM → lanes quiesce in order, views published (2 B6
rows), socket removed, lease released.

**Review round (Skeptic + Expert QA, codex; 21 findings, deduplicated to 13,
each verified in the tree before fixing).** The HIGHs were real:

- *The client was registered after the goal was committed* (both, HIGH):
  the executor's own poll could start the run and bind to no client, and
  the mission would say delivery failed while the client waited. Now the
  client is registered BEFORE the goal is visible in the journal (and
  unregistered if the commit is refused). Test: six back-to-back submits
  against a 1 ms poll all reach their submitter.
- *An idle connection held the quiesce* (both, HIGH): cancellation closed
  the listener only. Now every accepted connection is tracked and closed on
  cancellation, and a client has 30 s (why in code) to send its one line.
  Test: an idle socket, a partial line, and a waiting submit all release;
  `Stop` twice returns the same answer (the error is kept, not lost).
- *An interrupt pending after the last boundary was never acknowledged*
  (both, HIGH): now every pending interrupt EXPIRES when the execution is
  recorded (nothing is left to stop), committed by the driver; the fold
  refuses `expired` while the target still executes, and `consumed` at a
  boundary the attempt could not have been at (NOW: `before_execute` with
  no execute yet; AGENDA: `before_step_k` for exactly the next undone step
  of the plan). `expired` acks carry no attempt scope.
- *The driver stopped at the first interrupt even when acknowledged* (QA):
  the earliest UNACKNOWLEDGED one is consumed. Test: first consumed at the
  boundary, second expired at recorded.
- *`verdict.Current` never re-derived* (QA HIGH): it now `Check`s every
  resolution before choosing among them; a wire-valid forged one is refused.
- *Intake stored the thought before validating* (both): `run.ValidateIntake`
  is the one check both intake paths run before anything durable; a
  whitespace goal or an unknown lane stores nothing.
- *A submit saw a bare EOF when the process stopped* (both): waiting
  clients get a `stopping` event and `ErrStopped` naming their goal ("the
  goal stays journaled and continues on the next serve").
- *No retrieval surface for an orphaned payload* (both): `runs show
  <handle>` prints the latest delivery's payload from the store
  (`run.LatestPayload`), tested after a restart.
- *`ack` hid the dial error* (both): on a held lease it now reports both
  facts (the process holds the workspace; its socket did not answer, and
  why). *Negative `Poll`* refused at `Serve`. *Repeated identical executor
  failures* (Skeptic): three in a row make the lane fail (bounded restart,
  health line) instead of logging forever.
- Side-find while testing: a shutdown-cancelled invocation ("context
  canceled") was REUSED as the backend's failure on resume; now a
  cancellation is not the backend's answer and the call runs again.
- The AGENDA lane's generic pre-execute interrupt check was lane-agnostic
  and produced a boundary the fold rightly refused; NOW keeps
  `before_execute`, AGENDA has only `before_step_k`.

Tests: process 3 → 6, run +1, verdict +1 (14 packages green under
`-race`; contracts 0/0). Not done, recorded: durable lane cursors and an
explicit final-watermark record (a second front end); socket auth (the
socket lives in the workspace; its mode is the boundary); a re-present
verb that binds a new client to an orphaned run.

## Step 8 — fork/join over attempt refs, confined children, both policies, kills between every transition (2026-09-05)

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| `fork` (members fixed at creation; the barrier is exactly that set), `join_decision`, `cancellation_issued` (idempotent by fork+child), `child_terminal` (by the child attempt's own driver), `join_settled` (only when every member has a terminal) | §3 | kept; every transition keyed; the fold executes the causal order |
| Join policies `all`, `first_verdict` | §3 (v1: exactly these two) | kept; first_verdict decides EARLY (while losers run), cancels them by context and by record; losers end `cancelled` at their first boundary or `completed_late` |
| Child runs = child goals (`Parent`/`Root` set, origin `fork`, lane now) driven as goroutines under the parent's context | §3 | kept; the executor lane never drives a child directly |
| Siblings never share a mutable tree; confinement at the tool boundary | §3 | kept STRICTER than the design's v1: children run tool-less (the shell refuses ANY reported effect, query included — `ToolEffect.Refused`, invocation fails) because the only real backend (claude CLI) reports effects post hoc and cannot be confined to a working copy; children with their own working copy arrive with a backend that can be (a Finding) |
| Write sets, per-attempt working copies, the merge/apply step | §3 | **not in v1** (follows from the above: children produce answers, not files) |
| `PreparedEffectIntent` and its lifecycle (§19 item 3) | §3 | **not registered** — no producer while children are tool-less (L28) |
| Memory scope through ancestry (child → parent → root → workspace) | §3 | kept (`scope()` walks Parent/Root; children recall the parent's goal-scoped lessons) |
| Parallel steps in the plan (`{"parallel": [...], "join": ...}`) | how a fork enters v1 | kept; the planner prompt invites them for independent tool-less sub-questions; a fork step's `StepDone` cites the fork and its result is the composition of the selected members' whole responses |
| NOW with the model judge (`--judge-model`) | first_verdict needs a judge-standing `achieved` on a child | kept, additively (`ConfigSnapshot.Judge model` on lane now) |
| One heavy job at a time (D6) | §0 | the fork IS the parallelism: N children run concurrently by decision, as one job of the parent |

**Built.** `internal/run/fork.go` (5 kinds; registry at 36; contracts 0/0),
`ParsePlan` with parallel steps, `Driver.Confined/ChildOf/ModelJudge`,
`interrupted()` consuming a `CancellationIssued`, `recordedFailure()` (a
backend failure with its terminal committed continues as a failed
execution — this also stops a contract violation from being a lane
failure), fold rules for every fork record, `invoke.Invocation.Tools` +
`ToolEffect.Refused`. Step judge prompt: judges ONLY its step (the live run
showed haiku judging the fork step against the whole goal), and explains a
fork composition.

**Edge tests.** The two-level scenario (Warm up → parallel{A, B} → Wrap
up) under both policies with kills at 11 seams on the parent and the
children — each resumed to one delivered run, the fork settled, children
confined and terminal, the composition right for the policy, a second
resume a no-op; first_verdict with a member held in flight: the decision
lands while it runs, its invocation is cancelled and NOT re-executed, it
ends `cancelled` at its first boundary (attempt 2), with and without a
kill after the cancellation; a child reporting a Write is refused and
fails, the other is selected; door + fold fork vocabulary (one member,
foreign policy, decision selecting none, terminal from another attempt,
`all` decided early, settled before the barrier, cancellation without a
decision, a second fork at the step); invoke: tool-less requests refuse
effects. The keyed test backend answers by ordered prefix/substring rules
so concurrent children are deterministic. Two real hangs were found by the
tests and fixed: a blocked loser held the parent's wait (the parent's
prompts quote the plan, so a substring key matched them too); a crash
seam inside the decision left the other children live (any child error
now cancels every live child so the parent returns).

**Live.** `agenda --model haiku` on three independent one-sentence
questions "in parallel, then combine": the planner produced a parallel
step of 3, three confined children completed, `all` selected all three,
join settled, the step judged done, step 2 combined the answers, closure
achieved; cost 0.16 for the parent + 0.02 per child. B6: one row per
child run (task_type now, unjudged) beside the parent's — B6 has no
parent column; the relation lives in the journal.

**Review round (Skeptic + Expert QA, codex; 14 findings, deduplicated to 9,
each verified in the tree before fixing).** The HIGHs:

- *A crash between the decision and its cancellations re-executed a loser
  on resume* (both, HIGH): the repair of missing cancellations ran AFTER
  the children were driven. Now a decision's consequences are repaired
  BEFORE any member is driven. Test: member B held in flight, crash after
  the decision, resume — B executed once, ends `cancelled`.
- *The fold trusted the join decision* (both, HIGH). A `join_decision` is a
  DERIVED record: the fold recomputes the join rule over the fork as it
  stood (terminals, closures from the journal prefix) and refuses any
  difference — subsets, duplicates, wrong attempt refs, cancelling a
  terminal member all refused; `cancellation_issued` for an already
  terminal member refused.
- *Identical sub-goals collide* (QA HIGH) — REFUTED: goal ids are minted,
  not content-derived; the text is one thought. Pinned by a twins test.
- *No recover in child goroutines* (Skeptic): a child panic is now a
  contained error naming the member; every live child is cancelled; the
  parent's pass ends; resume completes.
- *Crash injection could not target one child* (both): `child:<n>:<seam>`
  fires in member n only; asymmetric kills and a three-member first_verdict
  (member order decides) are tested.
- *Parent usage excludes children* (both, MEDIUM): kept run-local and
  said so in the record's comment and here — each run's B6 row is its own
  receipts; a goal-tree total is a later fold.
- *A parent interrupt cannot stop an in-flight fork* (Skeptic, MEDIUM):
  stated as v1 behaviour — a fork settles before the parent's next
  boundary; the children can be interrupted individually by handle.
- *"completed_late is never learned from"* (Skeptic, LOW): reworded — it is
  never composed; no learner consumes fork results yet (step 9).

Tests: run +3 (decision/cancellation gap; asymmetric and three-member
forks; twins, forged decisions, cancellation of a terminal member, a child
panic). 14 packages green under `-race`; contracts 36 kinds, 0 errors / 0
warnings.

## Step 9 — tail lane and timers: observe → diagnose → propose (2026-09-05)

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| Observe after `recorded`: receipts, verdicts, usage, friction | §8 | kept as the tail's pure signal classifier over the run fold (`Signals`): unclear_goal, backend_failed, blocked_step, partial_output, interrupted, unjudged, not_achieved, delivery_failed, recovered, stuck, confined_effect |
| "deterministic classifiers → Observations" as separate records | §8 | **folded into one record**: a `diagnosis` carries the signals inline and the fold re-derives them (a signal is a pure function of the run's fold; one derived record per signal would be N records saying what one already says). The verdict package's `observation` stays what it is: a claim about a result |
| A model lens second → FailureClass | §8 | kept; the lens is a tool-less `diagnose` invocation of the attempt (reused across a crash by its receipt), and may name only a class the signals allow: the signal-established class, or — when nothing failed mechanically — `none`, `wrong_answer`, `incomplete_answer`. A malformed or failed lens leaves `lens_rule: no_lens:<why>`; no lens configured = `signals_only` |
| Propose a Learned revision at `candidate` (lesson, policy) | §8 | lessons kept (`learned_revision` provenance `tail`, Ref = the diagnosis, family = the run's); at most 3 per attempt. **Policy proposals wait for the policy apply surface (step 11)** |
| Learning from fork members | §3 | a `cancelled` or `completed_late` member gets a `tail_done{skipped}`: never diagnosed, never proposed from |
| Tenure candidate → observed | §7 | kept in the timers sweep (≥ 3 applications; evidence = the application that crossed the bound) — **cannot fire in v1**: only selectable revisions are recalled, so a candidate accrues no applications until experiments apply candidates (step 10). The rule is in place; its live proof is owed there |
| Expiry → tombstone | §7 | kept: a candidate or observed revision idle 30 days is tombstoned by the sweep (evidence = the revision). Design amended: `candidate→tombstone` added to the legal table (an idle candidate expires instead of accruing forever) |
| Timers: in-process sweeps with a Why, no cron | §10 | kept (`Timers` lane, stage 1, every 1m; idempotent by keyed commits) |
| Evaluator lane | §10 | not yet — step 11 |

**Built.** `internal/tail/{records,tail,timers}.go` — kinds `diagnosis`
(signals, class, why, lens, lens_rule) and `tail_done` (exactly one of
diagnosis | skipped; proposals), registry at 38; contracts 0/0. `Tail`
lane (stage 2; one pass per recorded attempt without a tail_done; the
diagnosis, its proposals and the tail_done in ONE keyed command). `Timers`
lane (stage 1; tenure + expiry; `learned_transition` actor `tenure` with
its own legal edges, refused at the door for anything else — and
candidate→observed is tenure's alone: the operator cannot write it).
`invoke.PurposeDiagnose`; `learn` source `tail` and actor `tenure`; `now`/
`agenda` run one tail pass in-process after the run; `serve` gets both
lanes. `invoke.Keyed` (the ordered-rule test backend from step 8, promoted
so the tail and process tests can build forks).

**Edge tests.** A recorded run is diagnosed once with deterministic signals
and the lens's class inside what the signals allow, proposals as candidate
revisions citing the diagnosis, a second pass a no-op, a crash after the
lens call reuses it; signals are a pure function of the fold (same run,
same signals, whatever the pass); the fold refuses forged diagnoses (wrong
signals, a class the signals don't allow without a lens, a lens that is
not a diagnose call of the attempt, a tail_done citing a foreign diagnosis
or a proposal that does not cite it); door vocabulary (foreign signal/
class, lens without its rule, both-or-neither in tail_done); a cancelled
fork member is skipped while the winner and parent are diagnosed and
nothing is learned from the loser; tenure does not touch a selectable
stage, expiry tombstones an idle candidate with a tenure transition, the
sweep is idempotent, tenure cannot promote.

**Live.** `now --model haiku` on a two-part question: closure unknown →
signal `unjudged` → lens class `incomplete_answer`, 3 proposals. The first
proposals were about the ENGINE ("route through a validation layer",
"escalate to manual verification"); the lens prompt now says a lesson is
one sentence of instruction to the model that answers a later goal like
this one, placed verbatim in its request — the re-run proposed "cite
authoritative sources for measurable quantities". `serve --model haiku`:
timers and tail lanes up; two submits 6s apart both diagnosed; SIGTERM
clean.

**Two bugs the live serve found, neither reachable from `now`:**

- *The executor's resume reconciled the tail's live lens call.* `Resume`
  runs restart reconciliation on every pass; with the tail holding a
  diagnose call open across a submit, the call was marked abandoned, its
  terminal then broke the invocation history, and the tail lane failed on
  every pass after. Reconciliation is now epoch-aware: the reader exposes
  each record's committing lease epoch (`ScanEpochs`), the invoke fold
  records the dispatch epoch, and only a dispatch from an EARLIER epoch is
  a dead process's. The invoke restart harness now re-acquires the lease
  (a new epoch) as a real restart does — it used to reopen under the same
  lease, which is why no test saw this. Tests: a call dispatched under this
  epoch is left alone by `Reconcile` and finishes; the same call seen from
  the next epoch is reconciled; a process test holds the lens open across
  a second submit and proves no reconciliation and the tail lane at gen 1.
- *A fold composed of several scans read two prefixes.* The tail's fold
  ran the run fold (to head H1) and then scanned tail records (to head
  H2 > H1) and saw a diagnosis citing a receipt the run fold had not read.
  `ProductionReader.Pin()` fixes a reader at the head; every production
  fold pins first (pinning is idempotent, so composed folds share one
  prefix). This was latent in every multi-scan fold since step 3½; only a
  concurrent writer exposed it.

**And a third, in the fork, that pinning exposed.** With folds pinned the
fork kill matrix started failing (4 of ~30 seams per run): the recorded
first_verdict decision cancelled a member the journal showed terminal
BEFORE the decision, so the fold refused it. The decision was computed
under the fork's in-process mutex (fold → decide → commit) but a sibling's
child-terminal commit was not, so it could land between the fold and the
decision commit; the slower pinned fold widened a window that was always
there. The fork now has an `order` lock that both take: a child's terminal
commit, and the decision's fold-decide-commit. The fold's decision error
now prints both sides (recorded vs the rule's), which is how this was read
in one look. Its resume-side twin: a member that finished (late) while
the process died between the decision and its cancellations needs no
cancellation — the repair now skips terminal members, since the fold
refuses a cancellation of one. Three consecutive clean runs of the fork
tests, the seam looped six times.

Tests: tail 6, invoke +1, process +1; 14 packages green under `-race`;
contracts 38 kinds, 0 errors / 0 warnings.

**Review round (Skeptic + Expert QA, codex; 24 findings, deduplicated to
12, each verified in the tree before fixing).** The HIGHs:

- *A diagnose receipt retroactively broke every recorded AGENDA outcome*
  (both, HIGH): the fold's usage rule summed EVERY receipt of the run's
  attempts, and the tail's diagnose receipt lands on the attempt after it
  is recorded. `now` compares only the producing receipt, so the live
  check missed it; `agenda` + tail would have poisoned the run fold. The
  tail's calls are the tail's cost: excluded from the goal's usage in the
  fold and the driver. Test: an AGENDA run diagnosed by a metered lens
  still folds.
- *`Tail.Pass` folded twice from an unpinned reader* (Skeptic, HIGH):
  the run fold and the tail fold pinned separately, so the diagnosis's
  signals could be over one prefix and the tail's done-set over another.
  The pass pins once.
- *Tenure transitions were wire-legal assertions* (both, HIGH): the learn
  fold checked only that the evidence was an earlier record. The bounds
  are now constants of the learn package (`TenureBound` 3, `ExpiryIdle`
  30d) so the fold re-derives both edges: a promotion's evidence must be
  the third application of that revision; an expiry must sit past the
  idle bound of the revision's last activity. Forged promotions (another
  revision's application) and premature expiries are refused.
- *`tail_done{skipped}` was free text* (both, HIGH): any attempt could be
  closed unlearned. The skip reason is derived from the fork's terminal
  for the run (a closed vocabulary at the door; the fold requires the
  terminal that derives it). A skip for a non-member is refused.
- *A transient lens failure closed the attempt signals-only forever*
  (both, HIGH/MED): a failed or unusable diagnose call now leaves the
  attempt open for the next pass, the failed call kept as evidence, up to
  `LensTries` (3); only then does it close as `no_lens:<why> (after 3
  tries)`. Tests: a blip then an answer; three forbidden answers.
- *The lens request was never re-derived* (both, MED): a forged diagnose
  call with an attacker's prompt could authorise a diagnosis. The fold
  re-renders `LensPrompt` from the attempt's evidence and requires the
  cited invocation's request address to equal it (`MaxProposals` became a
  constant so the render is exact); the producer reuses a call only under
  the same rule.
- *"One command" was the writer's claim only* (both, MED): the fold now
  requires a tail_done's proposals to be the lens response's, complete
  and in order, as workspace lessons of the run's family; a tail-provenance
  revision no tail_done proposes is refused at the end of the fold
  (commands are atomic frames, so a prefix never splits them).
- *A missing thought was a poison pill for the lane* (both, MED): a pass
  died on the first unreadable blob, restarting on it forever. An
  unreadable deliverable or lens response now closes THAT attempt with
  `tail_done{unreadable: <ref>}` (the fold requires the ref to be the
  attempt's evidence) and the pass goes on. A missing GOAL text is the
  run fold's own failure — corrupt run, not the tail's to close; stated.
- *`class: none` with proposals* (both, MED): refused at parse and so in
  the fold.
- *No bound on a pass* (Skeptic, MED): `MaxPerPass` 5; a pass that hits
  it runs again at once; the heartbeat moves every pass.
- *`ScanThrough` past a pin* (both, LOW): refused (`ErrBeyondPin`), with
  a reader test.
- *Same-epoch orphans* (both, LOW, SPECULATIVE): a dispatch of this epoch
  whose goroutine died without the process is never reconciled — stated
  in the code as the chosen direction (a live call is never corrupted; a
  stuck run waits for the next lease). Not fixed; an ownership registry is
  a later lift if a real path to it appears.
- Tests: the fork tail test now pins the winner's and parent's diagnoses
  (class none, no signals) so a leaked `interrupted` fails it.

**One the stricter fold found on its own.** With the prompt re-rendered
at fold time, the process test (tail lane vs executor resume) failed:
the tail had diagnosed an attempt between `recorded` and its prepared
delivery, so the producer's prompt had no deliverable and the fold's
did. That is not a prompt bug — a diagnosis over inputs that then move
can never be re-derived. The tail now reads only TERMINAL attempts
(`delivered` / `delivery_failed`: the point after which the signals and
the deliverable stop moving), and the fold refuses any diagnosis or
tail_done whose `Seq` is not past the attempt's terminal transition
(forgery: a driver crashed `after_recorded`, a pass that must not touch
it, a signals-correct diagnosis refused "before the attempt was
terminal"). Pattern, stated: a derived record is committed only once
the records it derives from are stable — same reason the fork serializes
its decision after its members' terminals.

Side-find: `supervise`'s stall test flaked once under the full `-race`
suite — it polled the in-memory watermark, which moves before the
heartbeat record is committed. Test now waits on the journal.

Tests: tail +2 (unreadable evidence; agenda after the tail's receipt),
forgeries +8, journal +1; 15 packages green under `-race`; contracts 38
kinds, 0 errors / 0 warnings.

## Step 10a — the policy apply surface: mechanisms as data at one boundary (2026-09-05)

Step 10 lands in two halves. This half is the second apply surface §7
names, built first because the experiment half needs a thing to ablate.

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| `policy` learned kind, versioned data consumed at ONE driver boundary | §7, D17 | kept: a `learned_revision` of kind `policy` carries a declared `PolicyRule{mechanism, enabled}` as a record field, not a thought (D16: process data is contract-tested; an unknown mechanism is refused at the door). `text` is now omitted for policies |
| Mechanism vocabulary | §7 "which mechanisms are enabled, decomposition depth, judge configuration" | **cut to two in v1**: `recall` (recall injection) and `model_judge` (AGENDA's separate judge backend). Decomposition depth and intent-check toggles would reach into the AGENDA loop's fold rules; they arrive when an `ablate(m)` experiment (step 11) needs them, not before |
| `PolicySelection{Run, Considered, Enabled, Basis}` | §7 | kept, plus `Snapshot` (defaults, then each enabled rule in item order) so the record says what the attempt ran with, not only who decided |
| `PolicyApplication{Item, Revision, Run, Snapshot}` | §7 | kept with `Selection` + `Rule` in place of `Run` + `Snapshot`: the proof cites the selection whose snapshot it contributed to; one per enabled revision, same command |
| "consumed at one policy boundary in the driver" | §7 | kept: the selection is folded and committed IN the attempt's command (attempt, created, selection, applications — atomic, so no attempt exists without its policy); `Config.Policy` and `Config.Mechanisms` on the attempt; two consumers — `Driver.judge(a)` and the recall query's `Off` |
| Policy-selection fold as versioned data over item effect | §7, §8a | the fold half is here (selectable ⇒ enabled); the item-effect half (`item_redundant` → tombstone → disabled) is 10b |
| Tenure/expiry over policy revisions | §7 | kept: `Exposures` (applications + policy applications, Seq order) replace applications in the tenure rule and `LastActivity` |

**Built.** `internal/learn/policy.go` — kinds `policy_selection`,
`policy_application` (registry at 40; contracts 0/0); `Mechanism`,
`Mechanisms` (defaults on), `PolicyRule`; `SelectPolicy` (pure; item
order; `Basis` = the transition that made each enabled revision
selectable); fold re-derives every selection, requires exactly one
application per enabled revision carrying that revision's rule, and
refuses a recall selection that does not obey its attempt's policy
(`Query.Off` → every item excluded `policy:recall_off`; a continuation
across a recall-policy change is refused). `run`: `ConfigSnapshot.Policy`
+ `.Mechanisms` (the door requires the whole vocabulary; with
`model_judge` off the judge backend must be the executor); `Driver.policy`
in the attempt command; `Driver.judge(a)`; the recall query reads the
snapshot; `run.Fold` refuses a config that disagrees with its selection;
`ReplayKey` clears the selection id (identity) and keeps the snapshot
(input). `maro-go learn add --policy <mechanism>=on|off`; `learn list`
shows the rule.

**Edge tests.** `learn`: door (policy with text, unknown mechanism, lesson
with a rule); candidate policy considered-not-enabled → defaults;
restamped → enabled with the transition as basis, snapshot flips, one
application, one exposure; recall under `recall=off` considers everything
and excludes it all by policy; a recall claiming the lesson under
recall-off, and a continuation across a policy change, refused; forged
policies (snapshot that does not re-derive, candidate enabled, application
for a non-enabled revision, foreign rule, enabled-but-not-applied, two
selections for one attempt, wrong standing). `run`: a candidate policy
changes nothing; a provisional `recall=off` makes the request the goal
alone with no application and the selection saying why; a provisional
`model_judge=off` runs all seven AGENDA calls on the executor, tool-less,
with the snapshot naming it; a forged config disagreeing with its
selection is refused.

**Live.** Scratch workspace: `learn add --policy recall=off`, staged
provisional; `now --model haiku` on a factual question answered normally;
the journal holds one `policy_selection` with `{"model_judge":true,
"recall":false}`, the recall selection with `"policy:recall_off":2`, and
zero applications.

## Step 10b — the experiment protocol: paired replay, attestation → measurement → lifecycle (2026-09-05)

The measured loop's unit (§8a, §9, §19): an immutable protocol, arms
that differ by exactly the hypothesis, a blinded oracle, an estimator
that is a fold, and a lifecycle transition that is DERIVED from the
measurement rather than asserted by anyone.

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| Experiment as immutable protocol in the control envelope: hypothesis (`ItemRev`), relation, population (a family), assignment kind, arms, predeclared outcome + analysis, `n`, oracle | §8a, §9, §19 | kept: `experiment` (control); nothing on it accrues — counts are folds over assignments, closure is its own record. `Version` + `Prior` (the prior attestation) carry the fishing guard (§19.4): one open experiment per hypothesis and population; a re-open is Version+1 |
| Assignment kinds | §8a "paired replay for deterministic units; randomized live assignment otherwise" | **cut to paired replay, fixed-n**: every unit is a past terminal production goal of the population family, re-run in both arms. Randomized live assignment and shadow arms (the experimental envelope, still unused) are step 11 |
| Oracle classes | §8a "deterministic fixtures, held-out blinded judge; the historical closure verdict is NOT an oracle" | **cut to `deterministic_fixture`**: score 1 when the deliverable contains the fixture text. The evaluator sees deliverable + fixture and nothing else — the hypothesis text is not an input, so the blinding is by construction and the verifier recomputes every score. The blinded model judge is step 11 |
| Outcome dimensions and estimators | §8a | one dimension (`fixture_match`, higher), one estimator (`paired_diff/1`: ITT over pairs with both outcomes, per-protocol over pairs whose exposure held as intended, discordance count; verdict by margin + `min_discordant` / `min_equivalent`). Missing outcomes: excluded (declared) |
| Unit evidence derived over the run fold: exposure, deliverable, artifact root, missingness — no scores | §19.1 | kept: `unit_evidence` (production), committed by the runner when the arm run is terminal; the fold recomputes all four from the run fold (derived only over stable inputs, pattern 46) |
| Cohort commitment: authenticated denominator + canonical protocol projection | §19.2 | kept: `cohort_commitment` (production) in ONE command with `cohort_closed` (control); the protocol is embedded so a production-only verifier never reads control. **Merkle paths cut**: the root is recomputed from the full unit list (fixed-n makes the list small) |
| Attestation: one row per cohort unit with evidence ids + scores | §19.3 | kept: `effect_attestation`; the fold recomputes every row (evidence ids, missingness, per-protocol exposure, both scores) from the evidence and the fixtures |
| Measurement as a deterministic fold; the ONE thing a measurement transition may cite | §8a, §7 | kept: `effect_measurement` implements `learn.EffectEvidence`; `learn.Fold` checks a measurement transition's item and edge (`StageFor`) against it without importing this package; `experiment.Fold` recomputes the measurement from the attestation |
| `item_redundant` → tombstone | §7 | kept: `provisional|effective|canon → tombstone` added to the legal table (design §7 amended); `equivalent` normalizes to `item_redundant`; a measured-equivalent selectable revision leaves the population |
| The tail never learns from an arm | §9 | kept: `tail_done{skipped: "replay arm: not learned from"}`; the fold refuses that skip on a non-replay run |
| Production-only verifier mode | §19.5 | **cut**: `experiment.Fold` reads both envelopes (control for the protocol and denominator). The production records carry everything a production-only verifier would need (embedded protocol, opaque control ids); the mode itself waits for a second reader |
| Exclusions / stopping rules beyond fixed-n | §8a | cut: v1 closes at `n`; a unit with a missing outcome is excluded from analysis, never from the cohort |

**Built.** `internal/experiment` — 7 kinds (registry at 47; contracts
0/0, three new field lines on `goal.replay`, `recall_selection.arm`,
`policy_selection.arm`; `learned_transition.actor` gains `measurement`;
`goal.origin` gains `replay`). `Open` (checks units against the run fold
and the hypothesis against the learned fold, applies the fishing guard as
the fold does); `Runner.Run` (sequential, D6: assign → treatment →
control per unit; every commit keyed; resumes a killed arm via
`ResumeRun`, an unstarted arm goal via `StartGoal`, a terminal arm by
deriving its evidence); `Close` (closure+commitment → attestation →
measurement → the transition `StageFor` derives; idempotent);
`Measure` (pure); `Score` (pure). `run`: `Driver.Replay` +
`ReplayOrigin` (presents to no one); a replay goal's parent is the unit
(fold: an earlier non-replay non-fork goal of the same root); recall and
policy selections carry the `ArmRef` (fold: exactly the goal's);
`Ledger.Replays` (one run per assignment and arm); `Resume` leaves
arm goals and runs to the runner. `learn`: `ArmRef` forced sets (apply
regardless of standing → `arm:withheld` / included), `EffectEvidence`,
`StageFor`, `ActorMeasurement`. `maro-go experiment open|run|close|list|show`.

**Edge tests.** `TestBlindedDiscrimination` — the design's end-to-end
(§8a): a keyed executor whose answer depends on exactly which lesson
text reached the request; a candidate "Answer in meters." applied over
three Everest units (fixture `8,849 meters`) measures treatment 3/3 vs
control 0/3 → `item_helpful` → effective; a candidate "Answer in feet."
over three K2 units measures `item_harmful` → quarantined; the next
production Everest request carries the effective lesson and not the
quarantined one and the delivered answer changed; every arm attempt's
tail is `skipped: replay arm`; a second open on the same hypothesis is
refused; re-close is idempotent (one transition). `TestEstimatorAndOracle`
— the seven verdict cases (one discordant pair is insufficient;
equivalence needs exposure; missing pairs excluded; unexposed pairs
count for ITT only), ablation sign flip, oracle edge cases.
`TestFoldRefusesForgedExperiments` — 13 must-detect fixtures over
snapshots of one honest history (re-open without the version bump /
with the wrong prior, a unit not in the protocol, a unit assigned twice,
an assignment after closure, evidence for the wrong arm run, a
commitment before the evidence is complete / with a foreign protocol,
a second attestation with an altered score, a second measurement with
the wrong verdict, a measurement transition moving another item / to
the wrong stage). `TestRunnerResumesAfterKill` — killed after
`recorded` (arm run non-terminal, no evidence) and after `intake` (arm
goal with no run): a plain `Resume` touches neither; the next `Run`
finishes with exactly six arm runs and Close measures. `TestOpenRefusesBadUnits`
— mixed families, unknown hypothesis, no units, not a goal, an arm as a
unit; a re-open is v2 citing the prior attestation.

**Live.** Scratch workspace, haiku. Two production NOW runs ("height of
Everest", "how tall is K2") both answered with feet AND meters; `learn
add "Give every height in feet only, never in meters."` (candidate);
`experiment open --relation apply --unit <goal>=meters --unit
<goal>=meters` over both (population `answer`, n=2); `experiment run
--model haiku`: four arm runs, treatment `exposed=true` answering in
feet only, control `exposed=false` answering with both units;
`experiment close`: `treatment_harmful → item_harmful (assigned 2,
analyzed 2, exposed 2, discordant 2, delta_pp -1.000)`, the revision
`now quarantined` by a `learned_transition{actor: measurement}` citing
the measurement. The next production run's recall excluded it
(`stage:quarantined: 1`, 0 included). Tail passes closed all four arm
attempts `skipped: replay arm: not learned from` (4 of 6 tail_done);
the arms produced no proposals. Journal census: 8 `experiment`-kind
records across the seven kinds, 35 goals, 7 attempts.

Residuals stated: `learn.Fold` verifies a measurement transition only
through the `EffectEvidence` interface, so a consistently forged
attestation+measurement pair passes `learn.Fold` alone
(`experiment.Fold` recomputes both; the production-only verifier mode
that would make this one reader is the §19.5 cut); the experimental
envelope stays unused until shadow arms (step 11); the runner is
sequential and one experiment at a time (D6).

**Review round (codex Skeptic + Expert QA, one pass, every finding
verified in the tree before fixing).** Nine real findings, all fixed
in the round:

- **A (high) — the arm's forced sets were never checked against the
  protocol.** A `ReplayContext` could apply a second item on the
  treatment or withhold the hypothesis on the control, every record
  re-derived, and the evidence read as an honest paired replay. The
  fold now walks every arm run (evidence or not) and requires each
  attempt's recall and policy selections to carry exactly the
  protocol's `ArmRef` — assignment, arm, apply set, withhold set —
  and the assignment to be a record that precedes the run.
- **B (high) — `Open` dereferenced nil** when the prior experiment on
  the hypothesis was closed but not yet attested (a crash between the
  closer's commits). It now refuses with "closed but not attested;
  finish its close first".
- **C — exposure was aggregated over all attempts** while the
  deliverable came from the terminal one; a lesson applied on attempt
  1 and dropped on attempt 2 counted as exposed. Evidence now reads
  the terminal attempt's selections only.
- **D — a superseded hypothesis ran silently unexposed** (recall
  forces items at their current revision, so the arm administered
  nothing). The runner refuses a stale experiment; the fold refuses an
  arm that started after the hypothesis was superseded.
- **E — a blank fixture** scored 0 on both arms and walked the item
  to tombstone through `item_redundant`. `Open` and the fold refuse
  blank fixtures.
- **F — closure and commitment "in one command" was asserted, not
  verified.** The first fix used Seq adjacency, which the test
  immediately showed is not the same thing (two back-to-back commands
  also produce adjacent seqs); the journal now answers
  `SameCommand(a, b)` from its tx acks and the fold asks it.
- **G — the oracle scored the failure envelope.** A failed arm
  delivers `Render(failed)`; a fixture of "failed" matched it. Evidence
  for a non-complete outcome now carries `missing: not_complete` and
  the pair leaves the analysis.
- **H — two units with identical goal text** shared a request hash;
  refused at `Open` and in the fold.
- **I — the forged-record tests never reached the fold's re-derive
  branches** (every forgery was caught by an earlier ordering check).
  Fixed with crash seams: the runner's `before_evidence` and the
  closer's `close|attest|measure|transition` snapshots hold the honest
  history up to but not including a record, so the forged first
  evidence / attestation / measurement is refused by "does not
  re-derive", "does not recompute", "not the estimator's fold". The
  same seams prove `Close` resumes at every commit boundary with the
  same measurement id and one transition.

Five new tests, two extended; `unit_evidence.missing` contract now
`^(no_deliverable|not_complete)$`. What the stricter fold found in the
existing scenarios: nothing — the honest runner's arms already carried
exactly the protocol's sets, which is the point of checking.

## Step 11a — randomized live assignment at intake, the blinded evaluator, the evaluator lane (2026-09-05)

Where D11 is actually established (§8a): a candidate is measured on the
production goals that arrive while it is open, not on replays of past
ones. The unit is the goal the user submitted; the arm is decided in the
same command that admits the goal; the user gets the deliverable as
always; a judge that sees only goal and deliverable scores it later;
the measurement's transition changes the next production run.

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| Randomized live assignment at intake: sequencer-enforced command, GoalID as randomization unit, keyed seed, mutual exclusion across experiments, admit + stop atomic with the goal | §8a, §9 | kept: `run.IntakeCommand` submits goal + assessment + assignment under `ExpectHead` and re-decides on `ErrPrecondition` (three tries); `experiment.Admit` is the `run.AdmitFunc` — first open live experiment of the goal's family with room and a current hypothesis; ordinal = count so far; arm = `ArmFor(seed, ordinal, first)` (permuted blocks of two: balance is by construction, no run of four one-arm goals). `goal.arm` replaces `goal.replay`: one field for both assignment kinds (a replay arm is a goal too) |
| The arm forced on the run, not suggested | §9 | kept: the goal carries the `ArmRef`; the driver hands it to policy and recall as before (step 10b); the fold checks a live arm's goal and selections against the assignment's protocol arm and refuses an unstarted goal whose arm is not an assignment's |
| Blinded evaluator oracle | §8a "held-out blinded judge" | kept: `blinded_evaluator` = `judge/1`, a tool-less `evaluate` invocation (new purpose) over the unit's own run, prompt = goal text + deliverable, reply `achieved|not_achieved`; the hypothesis text is not an input by construction. Rows cite the evaluate invocation; the fold recomputes the score from its response and refuses `unevaluated` when a usable evaluation exists |
| Estimator | §8a | `arm_diff/1`: mean(treatment) − mean(control) over per-protocol units; verdict by margin with `min_per_arm` (default 2) — the uncertainty control in place of an interval estimate, which is **cut** (n is small and declared; the per-arm floor is the honest bound) |
| Evaluator lane | §9 (the measured loop runs without an operator) | kept: `experiment.Lane` (stage 2, every 20s): for each open live experiment, derive evidence for terminal units; when the cohort is full, close, attest, measure, transition — the same `Closer` the CLI uses, resumable at every seam including "measurement without its transition" |
| Shadow arms + `HarnessChallenger` | §8a, D17 | **cut from v1**: a shadow arm needs experimental-envelope twins of the invocation records and only ever updates a challenger, never standing. D17 stays partial-by-scope: the bitter-lesson loop is inside the process for lessons and policies; harness challengers are a later envelope |
| `ablate(m)` equivalence → mechanism off | §9, D17 | → step 11b: seed mechanisms as canon policy items so `ablate_item` on a seed withholds the mechanism; equivalent → redundant → tombstone → the next `PolicySelection` disables it with the absence proof |
| Interval estimate, sequential stopping | §8a | cut: fixed n; per-arm minimums |

**Built.** `run`: `Goal.Arm *learn.ArmRef` (door: replay ⇒ arm + parent;
fork ⇒ no arm), `Driver.Admit`, `IntakeCommand`, `Ledger.Arms` (was
Replays), `KnownFamily`. `learn`: `ArmRef.Validate/Equal`. `invoke`:
`PurposeEvaluate`. `experiment`: kinds `randomized_live` /
`blinded_evaluator`, `Protocol.validate` per kind, `Assignment.Arm`,
`ArmFor`, `AnalysisSpec{MinPerArm}`, live `UnitRow` fields,
`EffectMeasurement.TreatmentN/ControlN`, `Spec.Live/Population/N`,
`Admit`, `Closer.Evidence`, `measureLive`, `EvaluatorPrompt/ParseEvaluation`,
`liveRow` (reuses a usable evaluation; up to 3 tries; `unevaluated`
otherwise), `evaluator.go` (`Lane`, `settled`), `fold.go` live rules
(dense ordinals < n; the unit's goal is a plain production goal taken in
by the SAME command — `Journal.SameCommand`; family = population; one
experiment per goal; hypothesis current at the assignment; arm = `ArmFor`;
goal arm = protocol arm; evidence arm = assigned arm; live attestation
rows recomputed from the cited evaluation). `process`: evaluator lane;
`submit` admits. CLI: `experiment open --live --population --n
[--min-per-arm]`, `close [--judge-model]`, list/show for live cohorts;
`now` admits too. Contracts 0/0 after `goal.arm`, `assignment.arm`,
protocol/evaluator/estimator patterns widened, `effect_measurement.
treatment_n/control_n`, `invocation.purpose` + `evaluate`.

**Edge tests.** `TestLiveRandomizedAssignment` — the loop end to end
with keyed backends: a write_local goal is not admitted; four answer
goals are admitted 2/2 with ordinals 0..3, treatment requests carry the
lesson and answer in meters, control requests do not and answer in feet,
recall carries the arm; a fifth goal runs plain; the runner refuses a
live experiment; one lane pass → `treatment_helpful`/`item_helpful`,
`treatment_n = control_n = 2`, delta 1, the revision effective; every
row cites an evaluate invocation whose request holds the deliverable and
NOT the lesson and used no tools; the next production goal is answered
in meters with no arm; a second pass writes nothing.
`TestLiveVerdicts` — harmful → quarantined; a neutral lesson →
equivalent → tombstone; a judge that never answers → three evaluate
calls per unit, every row `unevaluated`, `insufficient`, the item
unmoved. `TestLiveAdmissionIsAtomicAndResumes` — the head moves under
the admission twice (a lesson lands between decide and commit): the
sequencer refuses, the goal is re-decided and admitted; a third move is
the caller's error and production continues; a superseded hypothesis
stops admission (2/4 stays open, never closes); the closer killed after
`close`, `attest`, `measure` in turn — re-open refused mid-close, the
lane finishes with one measurement and one transition, four evaluate
calls for four units (seams never re-judge). `TestLiveFoldRefusesForgeries`
— an assignment outside the goal's intake command; the wrong arm; an
ordinal out of order; a goal arm forcing an extra item; a write_local
goal admitted; one goal in two experiments; evidence for the other arm;
and at the attest seam: a flipped score, `unevaluated` beside a usable
evaluation, a row citing another unit's evaluation, exposure flipped,
pair fields under a live protocol; `Close` without a judge is `ErrConfig`.
`TestOpenRefusesBadLiveProtocols` — units with live, population none /
unknown, n of one, no why, a live-admitted goal offered as a paired
unit; `ArmFor` parity and block balance.

What the tests found in the code: the commitment door indexed
`Protocol.Units[i]` for a live protocol (no units → panic on the first
live close); the lane skipped a measured experiment whose transition
had not been written (a crash between `measure` and `transition` was
permanent) — `State.settled` now asks whether the measurement reached
the item.

**Live.** Scratch workspace, `serve --model haiku --judge-model haiku`.
`learn add --family answer "Give every height in feet only, never in
meters."`; `experiment open --live --population answer --n 4`. Four
`submit`s (Everest, K2, Kilimanjaro, Denali): admitted
treatment/control/control/treatment; treatment runs `applied 1` and
answered in feet only ("Denali is 20,310 feet above sea level."),
control runs `recall 0 included of 1` and answered in both units. The
evaluator lane closed the cohort unprompted: `equivalent →
item_redundant (assigned 4, analyzed 4, exposed 2/2, delta_pp 0.000)` —
the blinded judge scored every deliverable achieved, so the lesson
changed the form and not the outcome; `learn list` shows the revision
`tombstone`. A fifth submit (Mont Blanc): `recall 0 included of 9` (the
tail had minted candidates meanwhile; the tombstoned lesson is out), no
arm. SIGTERM: lanes quiesced in order.

**Residuals.** The evaluator is the same model family as the executor
(haiku judging haiku) — held-out in inputs, not in weights; a second
provider is a config choice, not a code one. `Exposed` on a live row
means "ran the arm it was assigned" (per-protocol compliance), which
`show` now labels `as_assigned`. An experiment with a stale hypothesis
stays open forever at < n (nothing closes it); the operator surface
shows it, and a `stale` closure is a small later record. Admission
considers experiments in journal order, so two open live experiments on
one family serialize by age — mutual exclusion is per goal, not
fairness. Shadow arms and `ablate(m)` as above.

**Review round (Skeptic + Expert QA, codex; 14 findings, deduplicated to
11, each verified in the tree before fixing).** Three HIGHs were real,
one was out of scope:

- **A — a plain goal could fail intake because unrelated traffic moved
  the head** (both, HIGH). `Admit` returned the folded head even when
  it admitted nothing, so every production goal carried `ExpectHead`,
  and three busy commits between fold and commit failed the user's goal
  with no experiment involved. Now: no admission ⇒ no precondition; and
  `IntakeCommand` (`IntakeTries` 3, why in the code) decides twice
  under a precondition and then commits the goal PLAIN — a lost
  admission is dropped, never the goal. Tests: a goal that loses the
  head every time runs unadmitted; a goal of another family under
  constant traffic commits first time; two racing intakes on one
  cohort take ordinals 0 and 1 in opposite arms.
- **B — `serve` without `--judge-model` started an evaluator lane with
  no judge**, which died with `ErrConfig` on the first scoreable row
  (Skeptic, HIGH). `Options` already said "nil ⇒ Backend": the process
  now does that for the lane, and `Lane.Pass` logs "waits" and keeps
  the lane on a cohort it cannot score instead of returning the error.
- **C — protocols that could never reach a verdict were accepted**
  (both). `--n 2` under the default per-arm floor of 2, any odd n
  (blocks of two leave a 2/1 split). The protocol door now requires an
  even n with n/2 ≥ `min_per_arm` and ≥ `min_equivalent`, with the
  message naming the numbers. The old "judge never answers" test had
  opened n=2 and expected `insufficient` — it was passing on the
  degenerate protocol; it now runs n=4.
- **D — the tail learned from live arms** (Skeptic, MEDIUM; the live
  check showed it: 8 candidates minted from the four arm runs). The
  step-10b rule was keyed on `OriginReplay`; a live arm is a production
  run with `Goal.Arm != nil`. The tail now skips every arm and the fold
  refuses that skip on a run without one; the live test drains the tail
  over the arms (skipped, no proposals) and checks a plain run is not
  skipped.
- **I — the fold trusted the family assessment** (QA, HIGH). A forged
  intake command could carry a write_local goal with an `answer`
  assessment. `run.Classify` is deterministic over the goal text, so the
  experiment fold re-classifies the unit's text and refuses a
  population it does not classify as. Fixture: forged assessment.
- **K — AGENDA roots were admitted** (QA, MEDIUM): an AGENDA
  deliverable is a closure rendering, not the answer shape the judge
  scores. Live cohorts are NOW-lane goals, in `Admit` and in the fold
  (fixture: an agenda-lane goal).
- **L — a treatment that breaks execution vanished from the effect**
  (QA, MEDIUM). `not_complete` rows were excluded, so a candidate that
  made runs fail could measure helpful over the survivors or exhaust
  the cohort as `insufficient`. A run that did not complete did not
  achieve the goal: its row scores 0 with no judge call (door: a scored
  row without an evaluation may only be that zero; fold: only over
  `not_complete` evidence). Test: a treatment that fails every run →
  `treatment_harmful`, delta −1, quarantined; a completed run scored 0
  without an evaluation is refused.
- **F — `SameCommand` scanned every ack under the sequencer mutex**
  (Skeptic, MEDIUM). The journal now keeps each command's last seq in
  commit order (recovery and append) and answers by binary search.
- **J — the flagship test's judge keyed on the treatment's string**
  (QA, MEDIUM): with goals that named no unit, "helpful" was
  form-transport. The goals now ask in meters, so the keyed judge is a
  correctness judge; a new subtest asks without a unit and a judge that
  accepts either form measures the same lesson `equivalent`; a mixed
  cohort (treatment [1,1], control [1,0], built by choosing each
  block's second goal from the first's arm) measures delta 0.5.
- **G — one discordant unit decides at margin 0, n=4** (Skeptic, LOW).
  Deliberate for v1 and now stated here and in the mixed-cohort test:
  `arm_diff/1` at the per-arm floor is a sample-count gate, not a
  confidence bound; an interval or permutation rule is the upgrade
  edge when cohorts are larger than four.
- **Out of scope — `verdict.Check` proves a resolution over the
  candidates it NAMES, not over every candidate committed before it**
  (QA, HIGH; `internal/verdict/resolver.go` has no commits in this
  range). A forged closure resolution naming one `achieved` judge
  verdict and omitting an earlier `not_achieved` one re-derives. Real;
  queued: `run.Fold` should hand `Check` the full (subject, kind)
  candidate set with `Seq` below the resolution's and require set
  equality. Not fixed this round.

Refuted: none. What the stricter fold found in the existing scenarios:
nothing.

## Step 11b — seed mechanisms as policy items, `ablate(m)` by evidence (2026-09-05)

D17 inside the process for the harness's own mechanisms: a mechanism is
on because a canon policy item says so, and that item goes through the
same experiment door a lesson does. `ablate_item` on a seed withholds
the mechanism from the treatment arm; an equivalent measurement
tombstones the seed; the next production run's `PolicySelection` turns
the mechanism off, carrying the tombstone transition as its absence
proof. The driver never reads a config flag for this — it reads the
ledger.

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| Harness defaults as data | D17, §7 | kept: `learn.Mechanisms` defaults are all OFF; `learn.SeedRecords()` is one canon policy item per mechanism (`recall=on`, `model_judge=on`), provenance source `seed`, actor `seed` moving candidate→effective→canon citing the revision itself; `EnsureSeeds` commits them idempotently (`learn/seeds/1`) at `Driver.policy`, the CLI learn verbs, and `experiment open`. An unseeded workspace is a workspace with every mechanism off — honest, not broken |
| Seed rules the fold executes | §7 | kept: a seed is first, unrevised, workspace-scoped, any-family, `Enabled: true`, one per mechanism; a seed is never revised; the seed actor may only make the two seed moves; a seed transition cites its own revision. Forgeries in `TestSeedsAreTheHarnessDefaultsAsData` (second seed, seed off, seed revised, seed actor on an operator revision, seed transition on the wrong edge) |
| The absence proof | §7 "the selection says why" | kept: `PolicySelection.Excluded []Exclusion{Item, Revision, Stage, Basis, Reason}` — `standing` (basis = the transition that left it unselectable) or `arm:withheld` (basis = the assignment). The fold re-derives the list; dropped, misplaced, or restaged proofs are refused |
| Precedence between policy items | §7 | changed: `SelectPolicy` walks seeds first, then by the seq of each revision's deciding transition, arm-applied last — later decisions win, and a seed loses to any other selectable policy on its mechanism whenever either was created (a lazily-seeded workspace must not let the seed override an older operator policy). The door's "considered in item order" rule became "no item twice" |
| `ablate(m)` | §9, D17 | kept: `experiment open --mechanism recall\|model_judge --relation ablate --live ...` resolves the seed; `Open` refuses ablating a revision that is not selectable (both arms would run without it); the fold checks the same rule point-in-time off the transition seqs (the learned ledger folds whole, so "stage at open" is read from `Seq`) |
| Harmful ablation | §9 | kept: `TreatmentHarmful` under `ablate_item` normalizes to `ItemHelpful`; `StageFor(canon, helpful)` is none — the seed stays, the next run carries the mechanism |
| Shadow arms, `HarnessChallenger` | D17 | cut from v1 (11a) |

**Built.** `learn/seed.go` (`ActorSeed`, `SourceSeed`, `SeedKey`,
`SeedRecords`, `EnsureSeeds`, `Ledger.Seed`, `IsSeed`,
`checkSeedRevision`, `checkSeedTransition`); `records.go` actor/source
sets + the seed edge rule; `fold.go` `Ledger.seeds`; `policy.go`
`Exclusion`, `Excluded`, precedence sort, `samePolicy` over exclusions;
`run/driver.go` seeds before the policy selection; `experiment.go`
ablate-selectable rule; `experiment/fold.go` point-in-time twin; CLI
`learn list src=`, `experiment open --mechanism`. Contracts:
`learned_transition.actor` + `learned_revision.provenance.source` widened,
`policy_selection.excluded` (must-reject `reason: vibes`; measured_by
`TestSeedsAreTheHarnessDefaultsAsData`), report 0/0.

**Edge tests.** `learn`: `TestSeedsAreTheHarnessDefaultsAsData`
(unseeded ⇒ all off; `EnsureSeeds` idempotent, head unchanged; seeds
canon with two transitions; selection basis = the seed's canon
transition; operator tombstones the recall seed ⇒ `Excluded[0] ==
{seed, rev, tombstone, that transition, standing}`; forged selections
and forged seed histories refused). `experiment`:
`TestAblateMechanismByEvidence` — seeded production shows the effective
lesson in the request; under the ablation the treatment arm runs
`recall=false`, request == goal text, `Excluded` carries the seed
`arm:withheld` with the assignment as basis, `policy:recall_off` counted;
control carries the lesson; either-form judge ⇒ `equivalent →
item_redundant` ⇒ seed tombstoned by `measurement` citing the
measurement; next production run: recall off, request == goal text,
deliverable in feet, `Excluded == [{seed, rev, tombstone, the
measurement's transition, standing}]`; re-ablating the tombstoned seed
refused. Second harness: meters-asking goals + meters judge ⇒
`treatment_harmful → item_helpful`, seed stays canon, next run recalls.
Existing tests adapted for seeds in the population (`Considered` 3/4,
`kind:policy` 3, tail counts skip seeds via `learn.IsSeed`).

**Live.** Scratch workspace: `learn list` shows the two seeds canon
`src=seed` before any run. `learn add` a meters-only lesson, staged
effective; `experiment open --mechanism recall --relation ablate --live
--population answer --n 4`; `serve --model haiku --judge-model haiku`;
four submits (Everest, K2, Kilimanjaro, Denali): control runs `policy 2
of 2 enabled · recall 1 included of 3 · applied 1`, treatment runs
`policy 1 of 2 enabled · recall 0 included of 3`, alternating c/t/c/t.
The evaluator lane closed unprompted: `equivalent → item_redundant
(assigned 4, analyzed 4, exposed 2/2, delta_pp 0.000)`; `learn list`
shows the recall seed `tombstone`, the lesson still `effective`. A fifth
submit (Mont Blanc) ran `policy 1 of 2 enabled · recall 0 included of
3` with no arm and answered in both units — the lesson is in the ledger
and unreachable, because the mechanism that would carry it was switched
off by evidence.

**Residuals.** The harness's judge has the same model family as the
executor (11a residual, unchanged). `runs show` prints the payload, not
the selection — the absence proof is readable in the journal, not the
CLI. A seed is per workspace; a fresh workspace re-seeds from the
binary's defaults, so a "learned off" does not travel (portable learning
is a later step). Only two mechanisms exist; the seed set grows with
`learn.Mechanisms`, and the samples check pins that every mechanism has
a seed.

**Review round (Skeptic + Expert QA on codex, one pass; every finding
verified in the tree before fixing).** Nine distinct findings, seven
fixed same round, one refuted, one out of scope:

- A (both, high) — ablating a seed that an operator policy already
  overrides: both arms ran the same and an "equivalent" tombstoned the
  seed on evidence about nothing. FIXED: `experiment.armsDiffer` — at
  `Open` and at every `Admit`, an ablated POLICY item must be deciding
  (withholding it must change its mechanism's snapshot over the
  population); an overridden seed refuses to open and stops admitting.
  The fold cannot re-execute "deciding at open" (it has no ledger as of
  the open) — residual, stated in the fold.
- B (both, high) — seed identity was provenance alone: a hand-written
  `source: seed` revision promoted by operator edges became THE seed,
  and the honest seed command then bricked the fold as "a second seed".
  FIXED: a seed's promotion (from candidate, from effective) is the seed
  actor's exact edge citing the revision — no other actor promotes a
  seed, so a forged seed never becomes selectable; and `EnsureSeeds`
  folds first and writes only the mechanisms that have NO seed, so a
  forged candidate seed stays what it is (unselectable, its mechanism
  off with the candidate as the absence proof) and nothing bricks.
- C (both, high) — one fixed idempotency key for all seeds: a mechanism
  added by a later binary hid behind the old ack forever. FIXED: one key
  per mechanism (`learn/seed/<m>/1`), fold-driven backfill; pinned by
  the older-binary case in `TestSeedsAreTheHarnessDefaultsAsData`.
- D (Skeptic, medium) — `apply` on an already-selectable revision is the
  symmetric degenerate. FIXED at `Open`, at `Admit`, and in the fold
  (point-in-time off the transition seqs); the re-open test now
  tombstones the lesson first.
- E (QA, medium) — the contract sample had one revision both enabled and
  excluded, and the door validated exclusion fields independently.
  FIXED: sample corrected; door partition rules (every considered item
  enabled or excluded exactly once; `arm:withheld` needs the selection's
  arm and its assignment as basis; a candidate's standing exclusion has
  no basis, any other stage cites a transition). Door cases added.
- F (Skeptic, medium) — seed-forgery tests did not distinguish door from
  fold refusal, so a fold rule shadowed by the door was untested. FIXED:
  each case declares which surface refuses it; the fold cases land and
  the fold refuses the history. `Why` on seed transitions stays
  unconstrained (any non-empty text) — narrowed claim, not a rule.
- G (Skeptic, low) — `EnsureSeeds` on every attempt built records and took
  the sequencer lock. FIXED: it folds (the driver needed that fold
  anyway) and submits nothing when every mechanism has a seed.
- H (Skeptic, high) — a hypothesis superseded between intake and attempt
  start would run an arm that administers nothing and "report
  equivalence". REFUTED: `exposed(a, hyp)` makes such a row unexposed,
  per-protocol counts drop it, and the cohort closes `insufficient`
  (the stale residual of 11a, not a false verdict); a seed cannot be
  revised at all.
- I (QA, medium) — after a harmful ablation the seed stays canon and a
  new version can be opened at once; nothing bounds re-sampling until
  noise says "equivalent". OUT OF SCOPE for 11b (the fishing guard's
  versioning is 10b/11a design; "sequential stopping" was cut in 11a) —
  lead queued: a stopping rule or an accumulated estimator across
  versions of one hypothesis.

Live re-check after the fixes (fresh scratch workspace, haiku): seeds
listed per mechanism; `learn add --policy recall=off` staged provisional
makes `experiment open --mechanism recall --relation ablate` refuse
("does not decide recall over answer"); tombstoning the override opens
it; four submits c/t/c/t (`policy 2 of 3 enabled · recall 1 included of
4 · applied 1` vs `1 of 3 · 0 of 4`); evaluator `equivalent →
item_redundant`; seed tombstone; fifth submit `1 of 3 · 0 of 4`.

## Step 12 — native pack envelope, B7 lessons view, import quarantine (2026-09-05)

Design §13: shared edges EXACT to the wire contract, a native pack for
the Go-only causal history, and a quarantine through which learning
from elsewhere enters at candidate. Two directions, tested separately;
no round trip is claimed. The Python workspace's learned data (the live
store, read-only) imports at candidate.

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| B7 `lessons.jsonl` (flat `Lesson` shape) | §13 "shared edges exact" | kept: `learn.LessonsView` — a **whole-file** view (new `projector.WholeView`: the file is the learn fold at the announced head, rendered once; `LineView` is the old per-record kind; `New` refuses a view that is neither or both). One row per item whose CURRENT lesson revision is selectable or quarantined; field set and order are the Python dataclass's; `confidence` by stage orders as the ladder does; quarantined rows carry `contested{item_harmful, maro-go, at}` (B7's "on disk, not injected"); tail-learned rows `minted_from: outcome`; pack-entered rows `imported{source, why}` |
| Candidate/observed rows in B7 | — | cut, declared: the flat vocabulary has no non-injecting stamp that means "unproven" rather than "retired"; an unproven Go lesson must not become an injected Python one. Tombstones (archive) and policies (process data) also omitted |
| B7 medium/long tiers | B7 | cut: the Go ladder is the stage; no tier files |
| B1–B5, B8–B12 views | §13 mapping table complete | declared **not projected** with reasons in `contracts/VIEWS.md` (own root; run state is the journal; no producer) |
| Native pack | §13, §15 "pack import idempotency" | kept: `pack export <file>` / `pack import <file>` — JSON lines: header (`maro-go-pack/1`, head, counts), the cited `lesson_text` thoughts by hash (base64), then the carried records exactly as the source journal framed them (`journal.Encoded`: kind + envelope from the registry, body verbatim). Carried: learn revision/transition/application/policy_application + experiment/assignment/cohort_commitment/effect_attestation/effect_measurement |
| Import adopts the source's stage | — | cut by design: an import enters HERE as a fresh candidate (`Provenance{Source: import, Ref: source revision, Why: "pack <label>: item … was <stage> at head N"}`), text re-stored by hash, keyed `import/pack/<source revision>` — idempotent per source revision. Tombstoned/quarantined lessons, policies, and superseded revisions are not offered. Stage-at-source is read off the pack's own transition Seqs (pattern 68: read, don't re-execute) |
| Import atomicity | §15 | kept: the whole file is parsed, thoughts hash-checked (`ErrTampered`), and every record decoded through `journal.Decode` (registry kind/envelope, stored validation, seq agreement) BEFORE the first Submit; a foreign format (`maro-go-pack/2`, a Python `pack.json`), an uncarried kind, or a body that disagrees with its frame is `ErrFormat` and enters nothing |
| Python store import | "import the Python workspace's learned data at candidate" | kept: `pack import-python <dir> [--label]` reads the three B7 tiers read-only; highest tier wins per `lesson_id`; rows the Python readers would not inject are skipped by name (`minted_from=prompt`, non-empty `contested`, `provisional: true`); malformed rows counted, not fatal; `Why` carries lesson_id/tier/task_type/times_reinforced; keyed `import/python/<label>/<lesson_id>` |
| Python pack.py (tar.gz, `pack.json`, `REVIEW.md`, seal/scrub/adopt) | — | cut: the native pack is machine causal history, not an offer with a review sheet; adoption IS the standing ladder; content-addressed thoughts need no path rewriting |
| `provenance.source` vocabulary | contract | widened to `(operator|tail|seed|import)` (door + declared contract; report 0/0) |

**Built.** `projector/projector.go` (`View`/`LineView`/`WholeView`,
`writeView` renders a whole view from the pinned journal);
`journal/readers.go` `ProductionReader.PinAt(head)`; `learn/view.go`
(`LessonsView`, `LessonHandle`, `pyISO`); `learn/records.go`
`SourceImport`; new `internal/pack` (`Export`, `Import`, `ImportPython`,
`Report`, `Carried`, `ErrFormat`, `ErrTampered`); CLI `pack
export|import|import-python`; `LessonsView` registered in `journal
publish` and the serve loop's publish; `contracts/VIEWS.md` complete
(lessons row, not-projected table, carriers section).

**Edge tests.** `learn`: `TestLessonsViewIsB7Exact` — a view that
renders nothing is refused; rows lead with `lesson_id` (dataclass
order); the row set is exactly {provisional, effective (times_applied
1), canon (multi-line), contested, quarantined (+`contested`), imported
(+`imported`), the CURRENT text of a revised item}; candidate,
tombstone, superseded text and the policy absent; no tiered
`provisional` key; `recorded_at` is Python isoformat; a lesson landing
after the announced head is not in that generation but is after
republish. `pack`: `TestPackCarriesCausalHistoryAndImportsAtCandidate`
(header counts; 4 enter at candidate with fresh ids, `import`
provenance citing the source revision, family kept, source stage in
Why; quarantined/tombstone/policy skipped by name; local canon
untouched; recall excludes all four `stage:candidate`; re-import 0 new
/ 4 already, head unchanged, no command; the source revising alpha
re-enters as a new revision of the SAME local item back at candidate;
a later export offers only the new lesson; the first import is one
command spanning exactly its four records),
`TestPackRefusesWhatItCannotVouchFor` (foreign format, Python
pack.json, empty, header counts/thoughts/head disagreeing with the
body, duplicate record, tampered thought, uncited thought, non-lesson
thought, uncarried kind, seq disagreement, unknown line, and four
histories every record of which decodes but the fold refuses —
transition on the wrong item, from the wrong stage, a forged edge out
of quarantine, a sibling first revision, a measurement citing
non-evidence — each enters no record AND no thought; the untouched pack
still imports), `TestPythonWorkspaceImportsAtCandidate` (live-shaped
rows across three tiers: 7 enter in one command, medium wording beats
flat, `contested` `{}`/`false`/`null` are not contested (Python truth),
a flat `provisional: true` enters (the flat reader injects it), a
tiered one is skipped, a row missing a required dataclass field is
skipped as the reader skips it; idempotent with no command; the source
rewording a lesson under its lesson_id revises the same local item;
two stores with one basename are two sources; missing store is an
error). Full race suite green but one load flake, twice
(`invoke.TestTimeoutIsATerminal`: its 50 ms deadline expired between
the `prepared` and `dispatched` commits while the live check loaded the
box — a prepared-never-dispatched orphan for Reconcile by design, not
the hung-backend case the test is about; 3/3 green in isolation; the
budget is now 400 ms with the mechanism noted in the test).

**Live.** Scratch Go workspace: `learn add` ×2, `pack import-python
/home/clawd/.maro/workspace --label live` (read-only): **523 imported
at candidate; skipped contested 7, minted_from=prompt 1, provisional
9**; second call 0 new / 523 already. Staged one to provisional, one to
quarantined, one import to effective; `journal publish` → 3 B7 rows.
`pack export` → head 534, 531 records, 499 thoughts; a second
workspace imports 525 (the two seed policies skipped `policy 2`), then
twice more 0 new / 525 already. Python reader probe: the projected
file copied under a scratch `MARO_WORKSPACE/memory/`;
`memory_ledger.load_lessons()` loads 2 (the quarantined row excluded
by default, present with `include_contested=True` carrying
`{reason: item_harmful, source: maro-go}`), `task_type="qa"` filter
returns the family-tagged row.

**Review round (Skeptic + Expert QA on codex, one pass).** Findings,
each verified in the tree before fixing: (A) HIGH, both — the importer
trusted individually valid records, not a valid history (current = max
Seq, stage = last `To`, no predecessor/edge/evidence rules): FIXED —
`learn.FoldRecords` extracted from `Fold`; the pack's production
records fold under the ledger's own rules and the fold's ledger is the
only source of "current" and "stage"; the pack now carries the whole
learn ledger (recall/policy selections too) so the fold has what it
reads; header counts/head/duplicates checked. (B) HIGH, both — thoughts
were stored before records decoded, and N candidates were N commands
(partial entry on a failed Submit): FIXED — everything is parsed,
decoded, folded and hash-checked (`thought.HashOf`, no store) before
any write; one Submit keyed by the set it enters; `Report.Ack` spans it;
thought lines must be cited lesson texts. (C) MEDIUM — idempotency
keyed on `(label, lesson_id)` froze changed text, and same-basename
stores collided: FIXED — `Provenance.Origin` (structured source
identity; door: set exactly when source is `import`; contract
`used_for: identity`), re-import matches by origin, a changed text
under a known origin prefix revises the same local item, default label
= absolute path. (D) MEDIUM — Go's Python acceptance set was not the
readers': FIXED — required dataclass fields, Python truth for
`contested`, flat `provisional` injected / tiered skipped. (E) MEDIUM —
"never recalled before staging" is false under an experiment arm's
`apply`: REFUTED as a defect (that IS the door a candidate earns
standing through, design §9) — the claim is reworded in VIEWS.md and
here. (F) B7 "EXACT field for field" overstated (defaults omitted):
wording fixed in VIEWS.md/view.go; a Go `contested` stage carries no B7
stamp — declared. (G) view-test ordering loop was a no-op: replaced by
a real order assertion. (H) whole-file publish prefix: sound, both
reviewers; the post-swap debris on link failure is pre-existing
projector behavior, out of scope. Live arithmetic (523 = 540 − 1 − 7 −
9) independently recomputed by both reviewers from the live files.

**Residuals.** (0) A pack's experiment records ride and are decoded but
not folded (`experiment.Fold` needs the source's runs, which are not
carried); a forged attestation therefore passes as history — it decides
nothing here, since stages come from the learn fold and enter at
candidate regardless. (1) B7 rows for imported-then-promoted lessons carry the
source `Why` verbatim in `imported`; that is the whole provenance the
Python side gets — the source revision id is not a Python concept.
(2) `times_reinforced` is always 0: the Go ledger has no reinforcement
counter (exposures are `times_applied`); a Python reader that ranks by
reinforcement sees a flat store. (3) Experiment records ride in the
pack for causal history but nothing here folds them into this
workspace's experiment ledger (they cite runs and cohorts that do not
exist here); the pack is honest about what it carries and what an
import does with it. (4) The Python importer treats `task_type` as
provenance text, not a family: Go families are keyed differently, and a
wrong family would silently narrow recall.

## Step 13 — live acceptance: the §8a predicate, a lens swap, a metering target (2026-09-05)

**Intent.** Close v1 on the predicate the design set (§16 step 13):
one family, one live randomized experiment to its stopping rule, the
predeclared transition from a recomputed attestation, a later run whose
request hash or policy selection changed because of it, the Manti
target measured and reported, one mechanism removed with absence proof,
one lens swap. Two pieces were missing before it could be run honestly:
personas as lenses (§13) and metering targets with overage as an event
(§11, D13).

**Subtraction.**

| Considered | Where | Decision |
|---|---|---|
| Lens as a full persona object (voice, tools, memory) | §13 | cut: v1 lens = the exact text a judge/render request is prefixed with, carried by name + content-addressed `lens_text` thought on the invocation; neutral = no prefix (a neutral judge request is byte-identical to an unlensed one) |
| Lensing execute/plan/intent requests | §13 | refused at the door: a lens colours judgement and rendering, never what the work is |
| Budget enforcement (stop at limit) | §11, D13, D15 | cut: a target is measured on the recorded usage after `Recorded`; over ⇒ an `Overage` record before the delivery and a line in it; the run continues; the fold refuses a delivery prepared over target without the overage |
| Target per attempt / per invocation | §11 | cut: one target per goal, committed with the goal in the intake command; invocation-level `Target` (step 3) is unchanged and unrelated |
| Dimensions beyond cost/tokens/wall | §11 | cut: `cost_usd`, `tokens` (in+out), `wall_ms`; vocabulary at the door |
| A live `model_judge` ablation on agenda goals | §9 | not possible: live admission is NOW-lane roots only (11a); run on NOW answers, reported as such |

**Built.** `invoke.Lens{Name, Text}` on `Request`/`Invocation` (door:
named, `lens_text` kind, judge/render purposes only; validated before
the first write); `thought.LensText`; `run.Lenses` (neutral, skeptic),
`Lensed(text, prompt)`, `Driver.Lens`, `ConfigSnapshot.Lens`, judge
sites in the NOW closure judge and the agenda `invoke_` render under the
lens; the fold re-derives lensed verdicts (`want = Lensed(lens, want)`)
and `checkLenses` at `Recorded` refuses a lensed invocation whose name
is not the attempt's, whose request does not begin with the lens text,
or a judge request without the lens under a lensed attempt; fork
children inherit the lens. `run.MeteringTarget` (subject goal, name,
dimension, limit, why) and `run.Overage` (run-scoped, after Recorded,
measured = `MeasuredOn(recorded usage)`, > limit) with door and fold
rules; `IntakeCommand(…, extra...)`; `Driver.Target`, `meter` before
the delivery, `MeteringLine` appended to every delivery with a target;
`ParseTarget("dim=limit", why)`. `run.Inspect(rs)` — goal/family/arm,
lens, recall counts + included revisions + exclusion reasons, policy
enabled/excluded + mechanisms, every invocation's purpose/request
hash/model/lens/terminal, the metering verdict — printed by `runs show`.
CLI `--lens` (now/agenda/serve), `--target dim=limit --why` (now/agenda/
submit); `process.Request.Target/TargetWhy`, `Options.Lens`. Contracts:
`metering_target`, `overage`, `invocation.lens` declared; report 0/0.

**Edge tests.** `TestLensSwapOnTheSameFacts` — two NOW runs of one
goal, neutral and skeptic, model judge on: execute requests hash
identically; the skeptic judge request is byte-for-byte
`Lensed(lensText, neutralJudgeRequest)`; the invocation carries name +
`lens_text` ref; both closures re-derive through the fold; `Inspect`
names the lens; unknown lens ⇒ `ErrConfig` before any write; a lens on
an execute request, over a `prompt` thought, or nameless ⇒ refused by
the shell and by the door, head unchanged. `TestFoldLensRules` — the
seven shapes (neutral clean, lensed clean, judge without the lens,
neutral claiming a lens, wrong name, request lacking the prefix, lens
text absent). `TestMeteringTargetIsMeasuredNeverEnforced` — under:
line says under, no overage, target adjacent to the goal in its
command; over: overage committed (measured 2.5, limit 2, cites the
target), the line names it, the mission is still delivered, the event
fires; no target ⇒ no line; `MeasuredOn` per dimension; cost-not-
reported note; `ParseTarget` refusals (nine); door refusals (limit 0/
inf, dimension, no why, wrong subject, not over, NaN, unscoped) with
head unchanged; fold refusals on fresh replicas (target for no goal,
second target, overage measuring what was not recorded, overage twice,
citing another goal's target, on a run without a target, on an attempt
that does not exist). Full race suite quiet, exit 0.

**Live.** `planning/successor-acceptance.md` — every predicate row with
record ids, run handles, request hashes, and the four things the live
run taught (the blinded evaluator cannot score knowledge lessons and
tombstoned two that had changed behavior; the fixture oracle measured
the same lesson helpful; NOW executes inherit this repo's context via
cwd; live admission is NOW-only).

**Residuals.** (0) The oracle-class finding above is the v2 item this
step opens: a lesson that supplies a fact needs an oracle that can
check the fact. (1) `serve` never sets `ModelJudge` for NOW runs while
`now --judge-model` does — the same goal gets `closure unknown` under
the process and a judged closure in-process. (2) `Request.Cwd` is the
process's cwd for every execute; a per-workspace working directory and
an operator tool policy are owed before the Manti answer can match
Python's. (3) `pgrep -f "mg serve"` matched the calling shell's own
command line and killed it once during the live run — an ops trap,
not a code one (anchor the pattern).

**Review round (Skeptic + Expert QA on codex, one pass).** Findings,
each verified in the tree before fixing: (A) HIGH, both — a goal taken
in with a target whose run never started (crash after intake) lost the
target on resume: `StartGoal` built the `RunState` without it, so the
resumed run could exceed the envelope, deliver without an `Overage`,
and print no metering line. VERIFIED (`driver.go` `StartGoal`, no
`Target`; the fold set it only from `RunAttempt`). FIXED — the fold's
`Ledger` carries `Targets` by goal and `StartGoal` reads it; pinned by
`TestResumeUnstartedTargetedGoalIsMetered` (crash after intake, resume
with an operator spec that is gone, the overage and the line come from
the journal). (B) HIGH, both — the lens was bound by name only: the
attempt config carried `lens: skeptic` and the fold checked each
invocation's request against the invocation's OWN cited text, so two
different `lens_text` bodies under one name passed, and the verdict
re-derivation read the same self-cited text. VERIFIED. FIXED —
`ConfigSnapshot.LensText` (present iff `Lens`, a non-empty `lens_text`
ref; door-checked on `run_attempt`); `checkLens` runs per invocation
AS IT ATTACHES (not only at Recorded) and requires name AND text ref
equal to the binding, the request prefixed by the bound text, and
every judge/render request under a lensed attempt lensed; the verdict
re-derives under the CONFIGURED text. `checkLenses` at Recorded is now
the whole-set re-execution of the same rule. Pinned by
`TestFoldRefusesLensSwapsInHistory` (four forged invocations after a
real lensed run, each refused by `Fold`; four forged attempt configs
refused at the door) and the extended `TestFoldLensRules`. (C) MEDIUM
(Skeptic) — exported mutable `Lenses`: FIXED, unexported table +
`LensText(name)`; with (B) a rewrite could no longer alter a running
attempt anyway. (D) MEDIUM (both) — a cost target on a backend that
reports no cost printed "under": FIXED, `MeteringLine` says
`unreported (… no verdict)` and never "under"; pinned. (E) LOW (QA) —
fractional `tokens`/`wall_ms` limits made any use an overage: FIXED,
whole numbers at `ParseTarget` and the door; pinned. (F) LOW (both) —
lens name free text, zero-byte lens text accepted at the door: FIXED
(`^[a-z][a-z0-9_-]*$`; `Bytes == 0` refused as "the neutral lens —
carry none"); pinned. (G) MEDIUM (Skeptic) — two mutations survived
(delete the `checkLenses` call; no-op the delivery-over-target rule):
both now killed — the first by (B)'s attach-time check and the forged
histories, the second by
`TestFoldRefusesDeliveryOverTargetWithoutOverage` (crash after
recorded, forge a well-formed `DeliveryPrepared`, `Fold` refuses; the
driver's resume commits the overage first). (H) Report (both): the
request-hash row now carries the byte decomposition (after = before +
`\n\n## Recalled lessons\n- <lesson>`, re-derived by the fold's NOW
rule on every fold), the absence-proof row quotes the policy exclusion
entry (`Inspect` prints each `Excluded` entry: revision, item, stage,
reason), the `equivalent` closures read "per this evaluator", and the
date is the local date with the UTC stamp noted. REFUTED: none.
UNSETTLED: none that a probe here could settle — the reviewers could
not run `go test` (read-only sandbox) and said so; every test above ran
here. Residuals from the round: (4) replay arms run neutral (the
replay driver does not inherit the unit's lens) — a documented protocol
choice now, not a rule; an experiment that wants a lensed replay names
it. (5) An `evaluate`/`diagnose` invocation after Recorded is bound by
the door only (no lens on those purposes) — the attach-time check
covers whatever attaches, the door covers the rest. (6) (B) is a
wire-breaking change to `run_attempt` without a schema bump: the
scratch journal `l13` that the acceptance report cites no longer opens
under the fixed binary (its lensed attempt has no `lens_text`; the
door says so and refuses to open). No production journal of the
successor exists yet, so nothing is stranded; the report says which
binary reads it. The first production journal is where the schema-bump
discipline (`run_attempt/2`, a reader for `/1`) starts — before that it
is ceremony.

## Post-v1 item 1 — the work dir, the operator's tool policy, the execute frame (2026-09-05)

**Intent.** The acceptance run's fourth lesson: a NOW execute ran in
whatever directory the operator's shell was in — this repo's, so the
CLI inherited this repo's `CLAUDE.md` and answered a question about
gas stations as an engineer with a codebase in front of it. Two
operator controls were missing that Python has had since the first
month: where a run's process lives, and which tools it may reach for.
Jeremy: "yep, let's do this first."

**Subtraction.**

| Considered | Where | Decision |
|---|---|---|
| Per-run scratch directories (`work/<run>`) | — | cut: one `work/` per workspace; a run that wants isolation is a v2 seam, and a shared directory is what Python has |
| A tool policy persisted per workspace (config file) | D16 | cut: the policy is a process option (`--allow-tools`/`--deny-tools`, same on `now`/`agenda`/`serve`), recorded on every invocation as `backend.tool_policy`; a stored default is a later lift |
| Policy on the Request (per call) | — | cut: the policy is the backend's (rides `Capabilities`), so every invocation's record says what it ran under without the driver knowing about tools |
| Persist the work dir on the record | — | done as `invocation.cwd` (door: absolute when set) — the process shape is data (D16); a fold cannot check a cwd, but an inspect can show it |
| A system prompt for execute (Python's `_NOW_SYSTEM`) | §13 | done as a **frame**: bound in the attempt config by content ref like a lens, prefixed to the execute request, re-derived by the fold's exposure rule (`frame+goal+recall`); not operator-swappable in v1 |
| Framing agenda step requests | §13 | cut: an agenda step's request is the plan's rendering, already framed by the planner's contract |

**Built.** `invoke.ToolPolicy{Allow, Deny}` (`DefaultToolPolicy` = deny
`WebFetch,WebSearch`; `ParseToolPolicy`; canonical `String`) →
`Capabilities.ToolPolicy`; `Subprocess.Policy` → `--allowedTools` /
`--disallowedTools` on tool-bearing requests. `Request.Cwd` →
`Invocation.Cwd` (door: absolute). `Driver.Work` (default
`<workspace>/work`, created on first use) sets the cwd of every
tool-bearing execute and agenda step; forks inherit it; `serve`
defaults it from the root. `thought.FrameText`; `run.DefaultFrame`;
`ConfigSnapshot.Frame` (door: a non-empty `frame_text` thought);
`execute` sends `Lensed(frame, goal+recall)`; `checkExposure` resolves
the producing attempt's frame and refuses a NOW request that is not
frame+goal+recall; replay arms run under the unit's own frame
(`FrameOf`). `Inspect` prints `frame:`, `cwd=`, `tools=`.

**Edge tests.** `TestToolPolicyAndCwd` (invoke): default policy
string, parse, args, the door on a relative cwd.
`TestExecutesRunInTheWorkspaceWorkDir`: the recorded invocation's cwd
is the driver's work dir and it exists; a driver without one records
none. `TestExecuteFrameIsBoundAndReDerived`: the request bytes are
frame+goal+recall; the attempt config binds the frame's ref; the fold
accepts the honest invocation and refuses the same invocation claiming
the bare goal+recall (the pre-frame shape); the door refuses a frame
of another kind and an empty one.

**Live.** Scratch workspace `l14`. Before the frame, the Manti goal
under an open policy came back "I'm Claude Code … outside my scope" —
the bare NOW request, with nothing saying whose goal it was, let the
CLI answer as itself. With the frame and `--deny-tools ""`: a real
answer with three web sources (Utah is thin on ethanol-free fuel; the
Pure Gas app; SLC/Cedar City), cost 0.042 under a 2.00 target, cwd
`l14/work`, `frame:` bound. Under the default policy the same class of
question answered honestly that it had no web tool and gave the
Census route; the record reads `tools=deny=WebFetch,WebSearch`. The
default is the conservative one: an operator opens the web per
process, and the record says so either way.

**Residuals.** (1) `runs resume` builds its backend without the policy
flags — a resumed execute runs under the default policy, not the
operator's original; the original is on the record (`tool_policy`) and
resume could read it back from the attempt's last invocation. (2) The
frame is not operator-swappable and not lensable by name — one text,
bound by ref; a second frame is the first real test of whether the
lens table wants to become a prefix table. (3) The work dir is shared
across runs and never cleaned; nothing writes there yet except what a
run's tools write. (4) `tool_policy` is in `Capabilities`, so a
`scripted` backend records none — the tests' harness runs unpoliced
by construction, which is honest but means no fold-side rule can
cite a policy; policy is process-checked (`args`), record-visible,
fold-blind.

**Review round (Skeptic + Expert QA on codex, one pass).** Findings,
verified in the tree before any fix. VERIFIED and fixed same round:
(A) HIGH (Skeptic) — the fold never bound an invocation's backend
snapshot to the attempt's config, so a forged execute could claim any
tool policy (or `ActsOutward`, model) and `Fold` accepted it; the
policy was self-report. Now `checkBackend` runs as each invocation
attaches and again in the `Recorded` sweep: an execute ran on
`Config.Backend`; a judge/plan/intent/render call ran on the
configured judge backend (the executor when model_judge is off); the
tail's diagnose and the evaluator's evaluate are theirs and unbound.
Struct equality on `Capabilities`, so the policy string is part of it.
The driver satisfies it by construction — each attempt's config is
snapshotted from the same driver's backends that make its calls, and a
resume opens a new attempt — and every existing test passed unchanged.
(B) MEDIUM (QA) — `Capabilities.ToolPolicy` was record-visible but
not door-validated: `ParsePolicyString` reads the canonical form back
and `Capabilities.Validate` requires round-trip equality; the
invocation door and the attempt door (executor and judge snapshots)
call it. (C) MEDIUM (both) — the cwd test stopped at the scripted
backend, so removing `cmd.Dir = req.Cwd` survived: `TestSubprocessRunsInCwd`
runs a fake CLI that answers with `pwd -P` and asserts the response is
the request's cwd (and the engine's own cwd without one). (D) MEDIUM
(both) — the frame test called `checkExposure` directly, so deleting
the call at `Recorded` survived: `TestFoldRefusesUnframedExecuteUnderFramedAttempt`
forges a wire-valid twin of the honest execute (invocation, dispatch,
terminal, receipt) with the bare request under the framed attempt and
drives it through `Fold` and `Resume`, refused as "frame+goal+recall";
`TestFoldRefusesBackendSwapInHistory` does the same with the snapshot
swapped four ways. Seven must-detect mutations, all killed: drop the
`Recorded` exposure call; drop the frame from the re-derivation; ignore
`Request.Cwd`; drop the attach-time `checkBackend`; drop the deny list
from the CLI args; drop the policy door on invocations; drop it on
attempts. REFUTED: (E) HIGH (Skeptic) — "the frame is optional, so a
forged bare NOW history is accepted": an attempt with no frame and a
bare request is an honest history of an unframed run, recorded as such
(`frame: none (bare goal)`), the same standing as the neutral lens; a
forgery is a framed attempt with a bare request, and that is what (D)
refuses. Whether v2 requires a frame on every production NOW attempt
is a schema-bump question, noted under residuals. OUT-OF-SCOPE leads
(pre-existing fold rules, no in-range change exposes them; both worth
a must-detect fixture): (F) HIGH (QA) — a run-scoped verdict journaled
BEFORE its `RunAttempt` skips `checkJudgeVerdict` (`attemptNoErr`
returns nil and the verdict stays in the global map), so a later
Resolution can cite an unchecked judge verdict; probe:
`TestFoldRefusesVerdictBeforeItsAttempt`. (G) HIGH (QA) —
`verdict.Check` re-derives a Resolution from the candidates it names,
never against the complete committed set for its subject, so a
Resolution that omits a higher-standing verdict or a refuting
observation re-derives cleanly; probe:
`TestFoldRefusesResolutionOmittingCommittedCandidate`. UNSETTLED: none
the reviewers could not run `go test` (read-only sandbox) and named
the tests; every test above ran here. Residual added: (5) the
`Recorded`-sweep `checkBackend` is redundant defense behind the
attach-time one and no mutation kills it alone.

**Lead (F) closed the same day.** The out-of-scope verdict-ordering
lead was a one-line rule and every suite passed under it: a run-scoped
verdict that names an attempt not yet in the fold is refused as it
arrives ("does not exist yet") instead of being kept, unchecked, in the
verdict map; no writer of this engine journals a verdict ahead of the
attempt it judges. Fixture `TestFoldRefusesVerdictBeforeItsAttempt`
(a copy of an honest closure verdict re-addressed to attempt 5). Lead
(G), resolution completeness against the committed candidate set, is
a resolver-design question and stays open.

## Post-v1 item 2 — the oracle class is part of the hypothesis (2026-09-05)

**Intent.** The acceptance run tombstoned two lessons that had
demonstrably changed behavior: they supplied a fact ("the code word is
juniper"), and the blinded evaluator — seeing only goal and deliverable
— could not verify the fact, so it scored `not_achieved` in both arms
and the protocol called them equivalent. Jeremy: "you can have a tool
and not know how to use it and call it junk; that doesn't mean it's
useless, just that you're holding it wrong." The evaluator is kept and
matched to what it can judge; the oracle is chosen with the
hypothesis.

**Subtraction.**

| Considered | Where | Decision |
|---|---|---|
| A `knowledge` lesson kind the door could route on | learn | cut: `learn.LearnedKind` is lesson/policy and a lesson's text does not say which it is; the operator declares the oracle at open |
| Handing the evaluator the lesson text as the key | §9 | refused: the evaluator's inputs provably exclude the hypothesis (blinding); a fixture is deterministic, so blinding is moot |
| A per-unit fixture for live units | §9 | not possible: live units arrive at intake; the fixture is the protocol's, one expected answer for the population |
| Retiring `blinded_evaluator` for fact lessons | — | cut: it gains a third answer, `unjudgeable`, and says so instead of guessing |
| An oracle-competence threshold (too many unjudgeable ⇒ refuse the cohort) | — | cut: `unjudgeable` is excluded missingness, counted on the measurement; the per-arm floors already make such a cohort `insufficient`, which moves nothing |

**Built.** `Protocol.Fixture *thought.Ref` (live under
`deterministic_fixture`: the one expected answer; door: that oracle ⇔
a non-empty fixture thought and dimension `fixture_match`; the blinded
evaluator ⇔ no fixture and `goal_achieved`; paired replay carries none).
`Spec.Expect` / `experiment open --live … --expect <answer>` chooses
it. `liveRow` under the fixture oracle scores `Score(deliverable,
fixture)` with no evaluate call; `checkLiveRow` recomputes it;
the attestation's evaluator is `fixture/1`. `EvaluatorPrompt` gains
the third answer ("if achievement turns on a fact, file, or artifact
you are not shown … answer unjudgeable; never guess");
`ParseEvaluation` returns `(score, judged)`; an unjudgeable answer is
usable (no retry), lands as `Missing: unjudgeable` citing the
evaluation, is excluded from analysis and counted as
`EffectMeasurement.Unjudgeable`; the verifier re-reads the cited
receipt and refuses a judged score over an unjudgeable answer (and the
reverse). CLI list/close lines show the oracle and the count.

**Edge tests.** `TestLiveOracleIsPartOfTheHypothesis`: a fixture-oracle
live cohort (helpful, no judge, no evaluate invocation anywhere,
`fixture/1`, every score recomputed; a flipped score refused by the
verifier); the acceptance shape — a fact lesson under an evaluator that
answers unjudgeable — measures `insufficient` with four unjudgeable,
four evaluate calls (no retry), and the lesson stays `candidate`; a
partly judgeable cohort (one unjudgeable, three analyzed, helpful);
the door (blank expectation, expectation on paired replay, fixture
oracle without a fixture, blinded with one, fixture oracle scoring
`goal_achieved`). Five must-detect mutations, all killed: drop the
count; score unjudgeable as not_achieved; the verifier trusts a
fixture row's score; the verifier ignores judged-vs-unjudgeable; the
closer scores every fixture row 1.

**Live.** One direct probe of the real evaluator (haiku, no tools)
with the new prompt: on the acceptance shape (a code word in a file it
is not shown) one of two tries answered `unjudgeable` with the right
why ("requires verifying the contents of secret.txt, which was not
provided"); the other emitted tool-call text, which parses as unusable
and is retried, as before. On a judgeable answer (Everest, 8,849
meters) it answered `achieved`. So the third answer is one the
evaluator actually gives.

**Residuals.** (1) The evaluator fences its JSON in ```json — the
parser's `unfence` already strips that; what stays unusable is a reply
that narrates a tool call it has no tools for, and the tries bound
(3) is the only answer to it. (2) The fixture oracle on live is substring match
(`fixture/1`); a population whose answers vary in form (units, digits
vs words) wants a richer oracle — `--expect` is the seam. (3) Nothing
enforces that an operator who opens a fact lesson under the blinded
evaluator has chosen well; the measurement's `unjudgeable` count is
the honest signal after the fact, and a re-open (`version+1`) under
`--expect` is the remedy.

**Review round (Skeptic + Expert QA on codex, one pass).** Findings,
verified in the tree before any fix. VERIFIED and fixed same round:
(A) HIGH (both) — the evaluator prompt bytes changed under the
unchanged `effect_attestation/1` schema, so an honest attestation that
cites a judge/1 evaluation would no longer verify (the fold rendered
the current prompt): interpretation code changing under immutable
evidence is not a refusal of history. Now the evaluator is versioned
BY NAME on the attestation — `judge/2` asks three answers, `judge/1`
(the frozen `EvaluatorPromptV1`, two answers) stays readable — and
the verifier renders the prompt and reads the answer by the named
version (`Closer.EvaluatorVersion`, forwarded from the lane; door:
live under the blinded evaluator names one of the two, and a judge/1
row cannot claim unjudgeable). `TestEvaluatorVersionIsNamedOnTheAttestation`
closes a cohort under judge/1, folds it under the current code, pins
the V1 bytes, and refuses the same row under judge/2's name. No
production journal of the successor exists, so nothing was stranded;
the pattern is the point. (B) HIGH (Skeptic) / MEDIUM (QA) — the fold
never checked that a live protocol's fixture is a thought the store
holds (`State.experiment` walked the units, which a live protocol has
none of); a forged experiment naming a ghost fixture folded and would
have failed at close. Now checked in the fold;
`TestFoldRefusesLiveFixtureExperimentWhoseFixtureIsMissing`. (C)
MEDIUM (Skeptic) — `--expect` put the fixture thought before the flag
set was known to be a live cohort without units; the shape is checked
first now, the store written second. (D) MEDIUM (Skeptic) —
`EffectMeasurement.Unjudgeable` had no wire bound (the contract said
`-1` rejected; the door did not): now `0 ≤ unjudgeable ≤ assigned −
analyzed`. Four more mutations killed (skip the fold's fixture check;
drop the bound; render by the current version; let judge/1 rows claim
unjudgeable). REFUTED: none. OUT-OF-SCOPE, recorded: (E) HIGH (QA) —
lead (G) again, resolution completeness against the committed
candidate set (`verdict.Check` re-derives only from the candidates a
Resolution names; `Current` compares resolutions only with
resolutions), now flagged in two rounds — moved to the plan's queue as
owed before the first production journal. (F) MEDIUM (QA) — the
protocol-projection equality checks (experiment ⇔ commitment ⇔
attestation, pre-existing) have no end-to-end forged-record test;
one needs the closer to stop between commitment and attestation and a
hand-built attestation — named as `TestFoldRefusesFixtureAttestationWhoseFixtureDiffersFromCommitment`,
not built. (G) LOW (QA) — `verdict.Commit`'s refusals are proven on
`Resolve`, not on journal immutability after refusal. UNSETTLED: none
the reviewers could not run `go test` and named the tests; every test
above ran here. Residual added: (4) a unit whose run holds three
judge/1 evaluate calls (a crash across the version change) counts
them against judge/2's tries bound and closes `unevaluated` — honest,
and far-fetched before any production journal exists.


## Post-v1 item 3 — two-engine comparison (2026-09-05)

Protocol and ledger in `planning/successor-comparison.md`. Six goals
(four NOW, two AGENDA; every fixture script-checkable, pattern 84),
haiku through the CLI on both engines, fresh scratch workspaces on
both sides (`$SP/pyws`, `$SP/c3`), one run per cell. Both engines
6/6. Python $1.38 / 400 s, Go $0.25 / 256 s. NOW goals: Python one
call at ~$0.005, Go two or three at $0.02–0.04 (tools in the execute
system prompt + in-process tail diagnose). AGENDA goals: Python 17–22
calls at $0.55–0.80, Go 6–10 at $0.04–0.09 — Python's mechanism ladder
(cuts, scope, 3 decompose candidates, plan review, closure plan +
verdict, quality gate, claim review, 3 extractions) is a fixed cost on
a one-file goal; the successor's overhead is its process shape (intent,
plan, judge per step, closure judge, diagnose). Python's recon-first
plan and verification step are judgement the fixture cannot see; the
Go judges accepted the direct path, which was right here. Level gate
for the features-on-both-sides half PASSED. Nothing changed in the
tree for this item — the protocol document, the checker
(`$SP/cmpcheck.py`, scratch), and the ledger are the deliverable.


## Post-v1 feature 1 — lineage-scoped memory, both engines (2026-09-05)

Design note + per-engine ledger in `planning/feature-lineage-memory.md`.
Feature: `--after <handle>` makes a goal follow a prior run's lineage;
lessons minted from a run scope to the lineage root; recall walks own →
parent → root → workspace; promotion to workspace scope is NOT in this
slice. Go `30393f57` (`run.Lineage`/`LineageOf`, `Driver.After`, door +
fold rules for following goals, tail mints `ScopeGoal(root)`, Inspect
names the lineage); Python `73f4da36` (`TieredLesson.lineage`,
`recall.lineage_root`, `--after`, mint sites via `_lineage_root_for`,
`query_lessons_scored(lineage=)`). One review pass (Skeptic + QA): Go 2
HIGH, Python 5 HIGH + 3 MEDIUM, all verified and fixed same round (Go
`f4397e9b`); 12 + 20 mutants killed; race suite green; contracts 0/0;
Python fast suite green except the pre-existing date-dependent
`test_a_string_int_field_does_not_wedge_the_decay_cycle`. Live: both
engines' follow-ups recall the lineage lesson and a stranger does not
(Go `$SP/c3`, Python run 4fade67b in `$SP/pyws`). Side-find (Python,
BACKLOG): project identity is the goal slug, so an identically-worded
stranger inherits the lineage's Goal Ancestry block — lineage is
run-derived, project is text-derived, and they disagree. Next: feature
2 = related goals / run horizon (Jeremy's growth target), on both
engines, same ledger.


## Post-v1 feature 2 — the landscape (related runs), both engines (2026-09-05)

Design note + per-engine ledger + review ledger in
`planning/feature-related-runs.md`. Decree: the RUN decides its relation
to prior runs (fresh / related / rerun) from deterministic candidates +
one recorded judge call; `--after` is the override, `--fresh` the
opt-out; the goal-slug ancestry retired. Go `0133a8d0` (+ review fixes
this entry): `Landscape` record, `checkLandscape` re-derivation, two
history gates, versioned prompt template + parser (v3 strict);
Python `c19d619e` + `bd43ad31`: `src/landscape.py`, handle hook, lazy
hosted-free judge, `--fresh`, slug fallback retired. One review pass
(Skeptic + Expert QA): Go 2 fixed / 1 OOS-lead / 1 backlog, Python 5
fixed / 1 partial; 33 + 45 mutants killed; race suite green; contracts
0/0; Python fast suite green except the pre-existing decay-cycle test.
Live: both engines' follow-ups became `related` following the first
run on one haiku call, the unrelated goal stayed fresh with no call.
Wall: Go 78 min (design carried here) vs Python 23 min. Backlog from
this entry: version the intent/plan/step/closure templates; bind the
plan request byte-for-byte; a `Fresh` field on the goal record. Next:
wire Go as the challenger in Python's shadow lane on the live goal
stream.

## Post-v1 — comparison rerun + the Go challenger arm in the shadow lane (2026-09-06)

Jeremy: wire Go into the Python shadow lane "with a cap/off switch";
export/reseed judged not worth it (the landscape reads run history,
not lessons; imported lineage rows are quarantined) — a light rerun of
the comparison protocol on clean workspaces ran instead
(`planning/successor-comparison.md`, "Rerun"). Python 6/6 at $1.39
(card) / 402 s; Go 5/6 at $0.54 / 254 s; the landscape made the SAME
four decisions on both engines (R2 related → R1, R3 rerun → R1, R4
fresh with no call). The one failure was Go's and real: a tool-less
planner ran with no cwd, the CLI told it the launcher's directory was
"current", it baked that absolute path into a step and the tool-bearing
execute followed it — G4's file landed in the Python repo's checkout.
09-05 passed by launcher accident. Fix: `Driver.work` gives EVERY
request the work dir (this entry); acceptance = G4 rerun from the same
launcher, file in `<ws>/work`, none in the repo. Unsettled from the
rerun: Go's tail diagnose cost 3× per output token vs 09-05 with
`cache_read 0` (probe: the CLI's `modelUsage` on that call — the receipt
keeps only sums).

Go side of the arm: `RunState.Judge` (the landscape's attempt-0 call,
attached at the Landscape record), `run.Summarize` (`internal/run/
summary.go`: mission, landscape, lineage, every call with its receipt,
the usage sum with `cost_reported` false over any partial sum), and
`maro-go runs show --json <handle>` (the summary + the delivered payload
as `result`; the workspace announcement still comes first, the consumer
parses from the first object line). 7 mutants killed (judge detach,
partial-sum guard ×2, cost overwrite, attempt calls dropped, result
dropped).

Python side (main `99b3ba16` → this entry): `src/shadow_lane.py` Go
track — `ARM_GO`, own switch `shadow.go.enabled`, own claim dir
`<run-dir>/shadow-go/` (a SIBLING of `shadow/`: the star|plain track
claims by that dir's existence), own daily cap from its own `arm: "go"`
rows (`_today_ledger_count(arms)`), `go_eligible` (NOW primaries on the
basic checks — the engine runs with `--deny-tools` naming every
mutating/network tool, structural containment, no preamble; AGENDA
primaries through the star|plain read-tier gate), `run_go_challenger`
(engine on its own persistent workspace `shadow.go.workspace`, scratch
work dir under the claim, then `runs show --json` for the row: handle,
outcome, closure, calls, cost (None unless every call reported),
landscape relation, binary sha256 as the version pin). 8 mutants
killed. DEFAULTS.md rows, design doc "The Go track" with the
pre-registered questions and the prediction on record (Go ≥ harness on
NOW answers at lower cost; Go < harness on AGENDA depth until its
workspace has accrued lessons). Live flip not made: `shadow.go.enabled`
is a write to `~/.maro/workspace/config.yml`, Jeremy's.

## Post-v1 — the Go shadow arm goes live; operator-context parity (2026-09-06)

Jeremy flipped it ("Let's turn it on… 9 per day; effectively unlimited")
and widened it ("Widen the Go track's AGENDA eligibility to the
tool-policy containment too. If we're going to shadow, let's do it
right."): `shadow.go.enabled: true`, cap 9, both lanes eligible on the
basic checks, a cron tick every 10 min (no heartbeat process runs on
the box). First live pair, 37d0e041: the primary achieved a build-shaped
NOW goal at $2.89/786 s; the Go run asked "what is 'the maro box'?" at
$0.026/24 s. Not an engine gap — a CONTEXT gap: the champion's planner
injects the operator docs (`user/GOALS.md`, `CONTEXT.md`, `SIGNALS.md`)
and the challenger got none. CONTEXT.md answers that exact question.

Go side (this entry): operator context is a RECORDED INPUT. New thought
kind `context`; `Goal.Context *thought.Ref` (ValidateWire: a context
thought or nothing); `Driver.Context []byte` stored at intake and cited
by the goal record; `RunState.Context` rendered as a `## Operator
context` block and loaded at both fold sites (landscape and attempt —
the attempt site matters for `--after` goals, which never read the
landscape; the mutant that dropped it survived until that case was
tested); `rs.riders()` = context + related, replacing the five sites
that appended `rs.Related` (intent, plan, NOW execute, and their
re-derivations), so the fold verifies a request that carried context the
way it verifies one that carried a related run. `--context <file>` on
now/agenda; `Summary.Context` = the thought's hash. Contracts: `goal`
gains a declared `context` line (omitted / tolerated / identity /
unconstrained by D16). 9 mutants killed. Scope kept narrow on purpose:
the context rides into the requests that decide (intent, plan) and the
NOW execute; step executions and judges see their steps. Feeding it to
the executor's steps is a separate call, to be made on evidence.

Python side (main, same day): `shadow_lane._operator_context()` renders
the same docs the same way the planner does (overlay over template,
`clip(…, 4000)`), writes `<run-dir>/shadow-go/context.md`, passes
`--context`; row fields `context_docs` / `context_sha256` /
`context_chars` / `go_context` (the engine's hash — same bytes, so a
mismatch is a transport defect), `tokens_cached` (the diagnose-cost
question's denominator), and `go_reason` / `go_needs_clarification` /
`go_question` — a clarification is recorded as "asked", never acted on
(a shadow asks nobody). 8 mutants killed.

`scripts/install-maro-go.sh`: builds `./cmd/maro-go` from the checkout
into `~/.local/bin/maro-go` atomically (a sweep mid-exec keeps its old
inode), prints commit + sha256 (the row's `go_binary_sha256`), smoke =
`maro-go workspace`. The lane pins rows to the binary's hash, so a
landed Go change is not live until this runs — the Go side of the
"landed but not materialized" trap.

## Post-v1 — the secrets store, Go side (2026-09-06)

Decree (Jeremy, decision 5870f189): secrets management is the fix for
container blindness, not flipping the container off — "both in python and
go". The store is sops + age at `~/.maro/secrets/` (machine-level, engine-
neutral; `MARO_SECRETS_DIR` overrides), managed by the Python CLI; the Go
engine reads, injects, tells and ingests. `internal/secrets` is a leaf
(stdlib only, shells out to `sops`): names without the key, values with it,
the operator's `inject` policy (path.Match over ENV-style names — the same
set Python's fnmatchcase yields), the presence block with wording identical
to Python's, cleartext per-name metadata (origin operator|maro, run,
service), and the drop-file ingest (maro-derived, source `drop`). The
subprocess backend grew three seams: `Env` (tool-bearing calls only),
`Redact` (longest value first, response + transcript + reason) and
`AfterTools` (the ingest). `now`/`agenda`/`serve` wire the store through
`wireSecrets`; `maro-go secrets list|check|get` is the read-only view.
Live on this box the same day: 10 names, `YAHOO_*` injectable, both engines
agree on the check.

Patterns:

105. **Engine-neutral state lives beside the user config, not in either
     engine's workspace.** The Go engine never reads the Python workspace
     (§13); a secret is a property of the machine. `~/.maro/secrets/` next
     to `~/.maro/config.yml` is the one dir both engines can read without
     either owning it.
106. **Injection is per request kind, not per backend.** The same
     `Subprocess` serves judges and executes; `Env`/`Redact`/`AfterTools`
     key off `req.Tools`, so a judge never sees a credential and the
     ingest never fires for one. The test pins both directions with one
     fake CLI.
107. **A fake CLI that overwrites its capture is read by the last caller.**
     The NOW run's tail lens (diagnose, tool-less) also goes through the
     fake `claude`; `cat > prompt.txt` left the diagnose prompt where the
     execute frame was expected. Append with a separator; assert with
     contains.

## Post-v1 — the operator-question lane, Go side (2026-09-06)

Decree (Jeremy, decision 1d1ad8b0): the Telegram question loop gets built
out, "and it should be the rare exception, not the norm" — push toward
"maro answers its own questions as much as possible"; prompt-for-work
slips easily into prompt-for-decision/judgement/permission. Both engines.
The contract is the file the worker writes (`$MARO_ASK`, one JSON object:
question / why / no_input_alternative / tried), read by the driver after
the execute — never the worker's prose. Go: `internal/run/ask.go` (the
instructions paragraph, word for word Python's; `ReadAsk` / `ArchiveAsk`;
`Question` and `Answer` records; `askAfterExecute` on the NOW execute and
after every AGENDA step) — the attempt ends on an honest failed terminal
"needs answer: …", the tail treats it like "needs clarification" (no
signal, no lesson). `maro-go answer <handle> <text>` commits the Answer
against the asked run (refuses unasked / already answered; marks late past
the 24 h time box) and runs the goal again in its lane, lineage `--after`
the asked run, with the answer as operator context (`--context`), so the
follow-up is a recorded run of its own and the worker is told not to ask
again. `maro-go asks [--json]` is the ledger: pending / answered /
expired. `runs show` prints the question and the answer. Serve wires the
same path. Contracts: `question`, `answer` declared (0/0); tests: the
file contract, the wire vocabulary, and the loop end to end with a fake
worker that asks once.

Patterns:

108. **An ask is a file, not a sentence.** The driver reads `$MARO_ASK`
     after the execute; "I asked the operator" in the response without the
     file is not an ask, and a file without the sentence is. The same
     boundary as the derived-secrets drop: a worker's side channel is a
     path the frame names, and the engine reads it exactly once.
109. **Resume by running the goal again, after the asked run.** No paused
     process, no held lease: the answer is a record against the asked run
     and the follow-up is a normal run with the answer as operator context
     and the asked run as lineage. Everything the fold already knows about
     lineage, recall and the landscape applies unchanged; the only new
     state is two record kinds.
110. **A follow-up assertion finds its prompt by content, not by
     position.** The tail's lens calls come after the follow-up execute
     through the same fake CLI (pattern 107's sibling); "the last prompt"
     was the diagnosis lens. Search the captured calls for the answer
     block, and assert it rode into exactly one of them.

### Post-v1 — secrets hand-off file on the host lane (2026-09-06, evening)

Jeremy, on the secrets store: "ENV is great for docker in general... at a
general OS level a .env file is more siloed... from here it looks like
it's all the same; I think it's not in a security sense." He is right: in
a container the env is the silo; on the host the worker's env is inherited
by every tool shell and MCP server it spawns and is `/proc`-readable by the
same user. So the Go subprocess backend grew `invoke.HandOff`: before every
tool-bearing call the injected values are written to `<ws>/drop/secrets.env`
(0600, created O_EXCL), the child env carries only `$MARO_SECRETS_FILE`, and
the file is zero-filled and removed when the call returns (defer — every
path). `Subprocess.Env` now carries just the two paths. Python mirrors it in
`_run_subprocess_safe` (host lane, run scratch), same file name, same env
name, same presence wording.

- **111. The mechanism decides the wording.** `Presence(injected, file,
  drop)` says "Injected for this step as NAME=value lines in <path>" when
  a file carries the values and "Injected into your environment as
  variables" when the env does (the no-scratch fallback, and Python's
  container lane). A frame that says "variables" while the values are in
  a file sends the worker to `$YAHOO_USER` and an empty string — the
  false-absence failure this whole arc exists to remove, re-created by a
  stale sentence.
- **112. The leaf owns the lifetime.** `invoke` stays a leaf (no import of
  `secrets`): `HandOff` is three fields and two methods, and the shred is
  a `defer` inside `Complete` so a timeout, a parse error or a panic in
  the stream reader all leave no file. The caller (`wireSecrets`) only
  decides the path and the lines.

## Star skill v9 + its exercise: Go twins of the landscape-binding HIGHs (2026-09-16)

Jeremy: "upgrade the star skill in maro on the go port branch." The skill
(`.claude/skills/star/SKILL.md`) had sat at v8 since 2026-08-13 while 3+ arcs
closed (shadow lane, successor v1, secrets/ask lanes, the r1–r30
landscape-binding loop on main), which is the skill's own consolidation
trigger. v8 archived at docs/history/2026-09-16-star-skill-v8-pre-consolidation.md.

**Seven contract deltas, each from a landed arc:** done-means stated as
falsifiable claims with a positive control on absence-shaped checks
(HOUSE_STYLE 2026-08-16 + mutation-from-file); a fifth contract line, the
**1-shot bet**, settled at close by a **1-shot verdict** row (D17 — the star
skill IS the standing champion–challenger against the bare prompt); the
prior-attempt check becomes the **landscape trichotomy** fresh/related/rerun
decided from the record (2026-09-05 decree); worker identity as a taste
choice with a mandatory refutation when master and worker share a model;
the **twin-census refutation** at JUDGE for class-shaped deliverables
(patterns 17/19/20 — r27–r30 each found only twins of the prior fixes);
vary the WORKER, not the prompt, after a reject on a fix (flip decree
2026-09-13, tier decree 2026-08-22, r26 datapoint); the prescription audit
before routing on a delegate's recommendation (board-of-review import,
n=0 here). The version narrative collapsed into a ledger table; a currency
note records that the shadow lane's star|plain arm has ONE row since
08-14 and its ~10-pair adjudication never accrued.

**Exercise (the version rule demands one):** a real question for this
branch — which of the 21 Python HIGHs from r26–r29 have Go twins.
Record: planning/landscape-twins-go-2026-09-16.md. 2 delegations of 5,
blind positive control found, 22/22 rows probed. Verdicts: 3 twins
(one downgraded to partial), 9 guarded, 4 absent, 6 n/a-by-design.

- **113. Two Go twins to fix, one design question.** F9: the AGENDA step
  executor (`stepPrompt`, agenda_driver.go:332) and fork children get no
  Context/Related riders while intent, plan and the NOW executor do — an
  operator's answer reaches the plan but not the step that does the work;
  the fold re-derives the prompt byte-for-byte, so the fix is a template
  version bump. F20: `maro-go answer` commits the Answer, then launches
  the follow-up in a separate journal session; a failure there leaves the
  run answered-with-no-follow-up and `answer` refuses a retry — no
  `--retry` twin of Python's. F17 (partial): the related-run similarity
  scan reads `Goal.Text` only, so an answer never changes what a later
  run relates to — by the immutable-goal design; decide, don't patch.
- **114. The control must be defect-shaped.** The planted positive control
  was found, but the delegate recognised it as a control ("reads as
  fix-shaped"). A control that looks different from the real rows tests
  the instrument on an easier distribution than the one it is judged on.
- **115. Honest not_examined lines are coverage probes waiting to run.**
  The delegate's list of unread packages (tail, sheriff, learn) named the
  F1-class hiding place; a 30-second master grep closed it. The v9 rule
  ("a plausible missing class is a probe to RUN, not a caveat to write")
  paid for itself on its first run.

Owed on this branch: fix F9 (riders into `stepPrompt` + fork children,
prompt-template version bump, fold parity) and F20 (answer→follow-up as
one recoverable sequence, or a retry verb); census r30's five HIGHs
(6a09e8bd) against F19–F21.

## Post-v1 — LoopsBench item 1, Go side: the plan's declared prerequisites are an execution contract (2026-09-17)

Jeremy, the same morning: *"The current active dev branch should be the
succession branch. I think you're not targeting that and making changes
on the 'old' maro fork. We should correct that at the next opportunity."*
LoopsBench chunks 1–9 (2026-09-16/17) had landed on the Python mainline
(`main` e855c018). Correction applied here: the two LoopsBench gaps port
to THIS engine as features; chunks 2–9's substrate plumbing (atomic
checkpoint writes, resume claims / permits / consumption, NEXT.md mark
debt) is answered by construction on this side — the lease excludes a
second driver, the epoch stales a dead one, every commit is keyed and
idempotent, the fold is the checkpoint, and an in-flight invocation is
reused rather than replayed. Nothing of it ports.

**Subtraction artifact.**

| Item | Required by | Kept? |
|---|---|---|
| `{"step": <text>, "after": [k...]}` in the plan grammar; `Plan.Edges []StepEdge` (plan/2) | item 1: the planner's edges are a contract the executor honours | kept |
| The gate in the step loop: a step whose declared prerequisite did not end `done` is recorded `gated` (step_done/2: no invocation, no verdict, `gated_by`, a result that says so) and its dependents gate in turn | item 1; fold parity (a gated record re-derives from the plan and the earlier outcomes; an executed record must have had none) | kept |
| Sequential order stays soft (an `unclear`/`unjudged` step does not stop the next one); `blocked` still stops the run | v1 §5 behaviour; the Python gate's "sequential default soft unless configured" | kept, unchanged |
| A ready-frontier / DAG executor (run independent steps out of order) | Python chunk 1's parallel lane | **deleted** — this engine runs a plan one step at a time by design (§5); `{"parallel": …}` is the fan-out. Edges only GATE here |
| `[after:N]` text grammar inside a step's prose (Python) | Python planner convention | **deleted** — the plan is JSON; the field is typed, validated once at the boundary |
| Hard-gating on `unjudged` | the judge boundary refused its output: the prerequisite is not SHOWN done | kept — the fail-closed direction; the closure judge still sees the step's real result |
| Prompt template versioning for `stepPrompt` | F9's note: the fold re-derives the execute request byte-for-byte | **not needed** — a plan without edges renders byte-for-byte as before, so every earlier record re-derives; the "(after 1, 3)" suffix appears only for an edged step |
| Test seam `CrashAt = "stage#N"` (the Nth occurrence) | proving the reuse arithmetic after a gated step (the second execute) | kept; 8 lines in `crash()` |

**Build.** `run.StepEdge`, `Plan.Edges`/`EdgesAt`/`planAfter`,
`validAfter`; `StepGated` + `StepDone.GatedBy`; `ParsePlan` reads
`{"step", "after"}` and `after` on a parallel object; `planPrompt` teaches
the grammar ("declare only real prerequisites"); `stepPrompt` shows
"(after …)"; `gatedBy`/`gatedText` are the ONE derivation the driver
writes and the fold re-derives; `countExec`/`countJudge` replace
`len(prevSteps(prev))` in the reuse arithmetic (a gated step made no
call; a fork step no execute). Registry: plan → 2, step_done → 2;
contracts regenerated, `plan.edges` and `step_done.gated_by` declared,
outcome pattern widened; report 0/0.

**Edge tests (`internal/run/gate_test.go`).** The gate with its negative
control (step 1 unclear → step 3 gated, 2 executes, 5 judge calls, the
deliverable and the closure prompt carry the gating text, the executor's
plan shows "(after 1)", a restart over the gated record writes nothing;
step 1 done → step 3 runs); transitivity (gated → gated, the text names
"step 2 ended gated"); the kill matrix over a gated step
(`after_gated_step`; `after_step_execute#2` — the execute after a gated
step is REUSED on resume, not replayed); the plan boundary's ten
refusals + the accepted shapes; forgeries at the door (gated with an
invocation / without `gated_by` / by a later step; executed with
`gated_by`) and in the fold (a gated record over a done prerequisite; an
executed record over an unclear one).

- **116. Port the doctrine, not the code — and say which parts the
  substrate already answers.** Nine Python chunks reduced to one Go
  feature because the journal design (lease, epoch, keyed commits, fold,
  in-flight reuse) IS the resume machinery. Before porting a chunk, ask
  what its finding would look like on this side; if the answer is "the
  fold refuses that record", there is nothing to build.
- **117. A new outcome value is a schema version.** `gated` widened
  `step_done.outcome`; a v1 reader rejects it (`unknown_value:
  rejected`), so the kind is /2 and the driver writes /2 — old /1 records
  fold unchanged (readers accept 1..n). An additive omitted field alone
  would not have needed the bump; the vocabulary did.
- **118. Find the in-flight call by its REQUEST, never by counting.**
  The reuse arithmetic indexed the recovered attempt's invocations by a
  count of its steps; a step that made no call (gated; a fork step's
  execute) shifted the index past the landed call and the step was
  re-executed — a replayed external effect, the exact thing reuse exists
  to prevent. Counting the calls the steps consumed (this chunk's first
  fix) repaired the zero-call steps and left the class open: an attempt
  that REUSED a call and then crashed does not carry it in its own list,
  so the count and the list belonged to different attempts after a
  second recovery (r1, both lenses). The fix that closes the class:
  re-render the exact request the driver would send (judge prompts
  lensed), address it, and scan every attempt of the run that never
  recorded an outcome for that call with a receipt. The fold already
  accepted a reused call only by request parity; the driver now selects
  by the same key.
- **119. Render an addition only where it applies, and old records stay
  re-derivable.** A prompt the fold re-derives byte-for-byte cannot
  change for existing records; an edged step's "(after …)" appears only
  when there is an edge, so a plan without one renders exactly as
  before — no template version, no migration.

Review round 1 (Skeptic + Architect, codex gpt-5.6-sol): 3 HIGH, all
verified against the tree and fixed the same round — reuse after a
second recovery (pattern 118 as rewritten), a crash right after a FINAL
gated step (recovery took the last step's invocation — none — and the
closure could not record with a receipt; now the latest step that made a
call), and the fold accepting a `Fork` at a step whose declared
prerequisite did not end done (the gate was re-derived at the StepDone
only). 4 MED fixed: `ParsePlan` decided text-vs-parallel from zero
values; the rerun context dropped the prior plan's edges; the
`Outcome.Steps` comment lied; `planAfter` was quadratic. Queued as
design residue: the reader accepts `/1` records carrying `/2` vocabulary
(`record.Validate` takes 1..n and `ValidateWire` is version-blind — a
substrate change for every kind); the plan invocation's request is not
re-derived by the fold (pre-existing; needs versioned plan-prompt
templates so live histories still fold); structured gated counts / a
tail signal; an all-fork or fork-then-gated plan has no representative
execute receipt at all (the "representative invocation" Outcome design);
CLI-level coverage of the grammar.

Round 2 (one Skeptic on the fix diff): 2 HIGH + 5 MED, all verified
and fixed the same round. HIGH: request matching turned a recall-policy
change between a crash and its recovery (a different block → a different
request → no match) from a bricked journal (the old ordinal reuse cited a
call the fold's `stepRequest` then refused) into a REPLAYED outward call;
now the recovery fails closed, naming the landed call (`uncited()` — a
landed execute no step record cites — when the attempt does not continue
the recovered selection). HIGH: the NOW lane's `execute()` had the same
second-recovery hole (it walked only `prev.Invocations`); it now scans
every unrecorded attempt latest-first. MED: judge prompts carry no
ordinal, so two steps with the same text and result share a judge
request and the earlier step's unjudged call stood in for the later
step's — a step's judge is the call made AFTER its execute (attempt
`from` after invocation `after`, or any later attempt); a verdict an
intermediate attempt committed from a reused call was searched on the
call's attempt (a third recovery wrote a twin) — now found by
`Source.Ref`; the outcome stamped the resumer's model over a landed call
made by another (the fold binds them) — `lastModel` rides with
`lastExec`; `join` present-but-empty on a text step was accepted —
pointer; the fork judge had no crash seam — `after_fork_judge`, and its
test now asserts the verdict cites the landed call. Tests: three-attempt
kills for AGENDA (5 seam pairs, step verdicts counted once) and NOW, the
recall-policy flip (no replay, an honest failed outcome), the identical-
steps judge ordering, the model change over a final gated recovery.
STOP RULE: no round 3 (no regression; the residue above is design).

- **123. A request is not a stage key when two stages can render the
  same bytes.** Execute prompts carry the ordinal; judge prompts do not.
  Reuse by request alone let an earlier step's judge call answer for a
  later identical step. When the key can collide, order the search by
  the stage's own structure (the judge call comes after its execute)
  rather than widen the key on the wire.
- **124. A reuse rule has a lane twin.** The AGENDA fix left the NOW
  lane's `execute()` with the identical second-recovery hole. Census
  every lane's reader of "the recovered attempt's invocations" when the
  rule changes.
- **125. When a recovery cannot reproduce a landed call's request, fail
  closed naming the call.** Re-rendering under a new selection and
  finding nothing is not "nothing landed"; the honest outcome is a
  failure that cites the call, never a second effect.

- **120. A forged record stays in the journal: one history per forgery.**
  Three plan forgeries submitted to one journal were all refused for the
  FIRST one's reason and two of the three expectations passed by
  coincidence. `forge` submits before it folds; a must-detect fixture
  that reuses a harness across forgeries is testing the first forgery
  three times.
- **121. Presence, not zero value, decides a grammar.** A step object was
  read as text-or-parallel from decoded zero values, so `{"step": "b",
  "parallel": []}` was a text step and `{"step": "", "parallel": [...]}`
  a parallel one. When the shape is chosen by WHICH key is present,
  decode into pointers and require exactly one.
- **122. Re-derive a gate at every record that can precede the gated
  one.** The fold checked the gate on the `StepDone`; a parallel step
  emits a `Fork` first, and a forged fork ran children through an
  otherwise accepted history. When a stage can emit records before its
  terminal one, the gate is re-derived at each of them.

Owed on this branch: LoopsBench item 2 (regression obligations — re-run
what a step verified, over the recorded `tool_effect` rows, at restart
and closure); the r1 residue above plus "continue the producing
selection" as the non-failing answer to pattern 125; F9, F20, the r30
census (unchanged).

## Post-v1 — LoopsBench item 2, Go side: what a step proved is re-run at closure (2026-09-17)

**Ask (Jeremy, 2026-09-17, afk):** "re-implement the missing/intentional
things we've added to main and have not for whatever reason on
successor" — by contract, not line by line. The audit
(`planning/successor-audit-2026-09-17.md`) put this first: the Python
main closed it 2026-09-16 (chunks 2–9 of the LoopsBench arc) and the
Go engine had item 1 (the gate) but not item 2.

**The gap (LoopsBench):** step 1 runs the suite and sees it pass; step 2
edits; closure believes "achieved" because the closure judge reads the
steps' own results. The proof is stale at the moment it is trusted.

**Contract kept from main (audit §2):** an obligation is the exact
runner argv a FINAL-done step ran in a recorded cwd, taken from real
shell tool events with POSITIVE evidence (a result seen, not an error,
non-empty, no failure tally, the family's pass tally where it has one);
the literal grammar `[cd DIR &&] [NAME=value…] [uv|poetry|pipenv run]
RUNNER ARGS` and nothing programmatic; re-run at closure with no shell in
the recorded cwd; tally-first classification; a Fail downgrades an
achieved closure; an inconclusive re-run never downgrades and never
vetoes.

**The Go engine's own road (D1, D5):**

- `internal/regression` (pure): `Parse`, `Passed`, `Classify`, `Rerun`
  (`exec.CommandContext`, no shell, `WaitDelay` 2s, dir-missing / not
  started / timeout ⇒ Inconclusive with a `Why`). No policy mechanism,
  no kill switch, no cap on obligations (D13/D15, "off switches stay
  off").
- Obligations are DERIVED at closure, never carried: from the folded
  invocation states (`invoke.Fold` over the journal — the driver's run
  state, folded at start, does not carry this attempt's own calls) of
  the done steps (AGENDA) or the one complete execute (NOW); the tool
  effect's input/output ride the shell's byte-preserving evidence
  envelope (`invoke.DecodeEvidence`).
- `Driver.regress` re-runs each obligation BEFORE the closure judge and
  commits, per obligation, ONE journal command holding the
  `regression_rerun` record (argv, env, dir, exit, timed_out,
  stdout/stderr as `thought.Evidence`, outcome, why) and the
  `verdict.Observation` it grounds (check `regression_rerun`; Fail ⇒
  refuted@1, Pass ⇒ supported@1, Inconclusive ⇒ could_not_observe@0).
  Neither exists without the other. A re-run an earlier unrecorded
  attempt made for the same (argv, env, dir) is reused, the judge-verdict
  rule.
- The closure prompt gets a `## Regression checks` section (byte-identical
  to before when there is no obligation); the observations ride
  `AttemptState.Observations` into `verdict.Commit`, so a Fail refutes an
  `achieved` closure MECHANICALLY (resolver rule
  `refuted_by_observation:regression_rerun`) — not by persuading the
  judge.
- The fold re-derives: a re-run must cite an obligation the folded steps
  derive (same step, dir, argv, env), once; a classified outcome must
  equal `Classify` over its own stored bytes; an observation must cite a
  re-run of its attempt and say what the re-run says, once; the closure
  verdict's prompt parity appends the re-runs the driver showed
  (`closureReruns` mirrors the reuse rule). Crash seam `after_regression`.
- Registry: `thought.Evidence`, `verdict.CheckRegressionRerun` (closure),
  `regression_rerun/1` declared + generated; `contracts report` 0/0.

**Tests:** grammar must-detect fixtures (programs, non-exec forms incl.
`--help`/`--version`, make clusters and `--dry`-style abbreviations,
control env like `MAKEFLAGS=-n`, wrappers kept in the identity), positive-evidence table, tally-first,
real re-run in a temp dir (Makefile flipped between step and closure;
missing runner / dir / timeout / signal / cancel / truncated-pass
inconclusive, truncated-fail a Fail; a recorded `PATH=bin` and an empty
`PATH=` select the runner and never the host's; a background writer and
a grandchild die with the group); run-package: a refuted
achieved closure and its passing negative control (resolution names the
observation; `runs show` line; re-derives after restart), seven
no-obligation shapes render the prompt byte-for-byte, dedup + inconclusive
moves the closure nowhere, the kill at `after_regression` reuses the one
re-run (Makefile repaired in between — a second run would have passed),
the NOW lane's execute is an obligation too, eleven forgeries refused
(no such obligation, outcome disagrees, a why / a truncated flag / a
signal death launder nothing, an observation citing another effect or
the re-run alone, an orphan re-run, a re-run of what an earlier attempt
already re-ran, a disagreeing observation), wire validation, a JSON
round trip with `"step":0` explicit.

**Review (codex gpt-5.6-sol, high; 3 rounds, stopped per the 2026-09-16
budget decree):** r1 = Skeptic + Architect on the chunk (10 + 9 findings,
the first pass timed out at 900s on a 70KB prompt and three leads were
salvaged from its event stream; the slim 44KB re-issue finished in ~18
min). Verified and fixed: a silent runner (exit 0, empty output) was
Pass — now Inconclusive, and Classify demands the family's pass tally;
the re-run resolved the runner through the ORCHESTRATOR's PATH and the
process env, not the recorded `PATH=…` assignment and the shell's tool
env — now `lookPath` over the recorded PATH, `PWD`, and the backend's
`ToolEnv` (secrets drop, ask file); the fold's outcome check only fired
on Fail — now a total state table (`TimedOut || Truncated || Exit < 0` ⇒
Inconclusive, else `Classify` over the stored bytes); `go test` pass
tallies were pytest-shaped — `ok`/`PASS`/`no test files`/`-json`
Action:pass; `Key` did not quote, so `A="x B=y"` and `A=x B=y` collided;
non-executing forms (`make -n/-q/-t`, `tox -l`, `pytest --co`, `go test
-list`, `cargo --list`) were obligations; the process group was not
killed with the leader and a cancelled context read as a timeout;
capture was unbounded; an observation could cite any tool effect —
now exactly `[regression_rerun, tool_effect]` with the re-run's own
effect, and a re-run with no observation is refused; the NOW lane
looked only at its own attempt's execute (mutation-checked). r2 = one
Skeptic on the fix diff (8): the PATH lookup fell back to the host's
`pytest` when the recorded one was gone (now Inconclusive "runner not on
the recorded PATH"); `runs resume` built a bare subprocess without
`wireSecrets`/`wireAsk` and with the 10-minute test timeout; a truncated
capture could still classify (now Inconclusive); `TimedOut` with an exit
code passed the wire; a redirected background child outlived the
re-run; `--help`/`--version`, clustered make flags (`-nk`) and
`MAKEFLAGS=-n` / `PYTEST_ADDOPTS=--co` / `GOFLAGS=-n` slipped the
grammar; completeness checked only the shown re-runs. r3 = one Skeptic on the r2 fix diff (4; the regression
round the decree allows): the `Truncated` flag skipped the fold's byte
check, so a forged record could launder a failing tail as inconclusive
— now `Decide` (tally-first even under truncation: a failure tally or
non-zero exit is a Fail, only a pass tally in the tail is inconclusive)
is the ONE classification the writer and the fold share, and the fold
refuses the flag over a capture shorter than `MaxCapture`; an explicitly
EMPTY recorded `PATH=` was read as "no PATH" and fell through to the host
runner — now a set-but-empty PATH is the dir; `make -kh`, `make -v` and
the getopt_long/argparse abbreviations (`make --dry`, `pytest --collect`,
`tox --listenv`) were obligations — now refused (a prefix of a
non-executing long option, in the abbreviating families); the group
kill was fired and assumed — now the result waits, bounded at 2s, until
nothing in the group answers signal 0, and a Pass over a group that did
not exit is inconclusive. One r3 claim was refuted on read: the
grandchild fixture's `$$!` is a Makefile recipe, which make unescapes to
`$!` before the shell runs it. Stopped after r3 per the round budget.

Full suite: 19 packages ok, twice; the one red in the last-but-one pass
was `supervise` `TestPanicIsContainedAndRestartIsBounded`, untouched by
this chunk and green 3/3 in isolation — a pre-existing race (the lane's
`gaveUp` flag flips under the lock BEFORE the `gave_up` event commits,
so a reader that waits on the flag can read the journal a beat early;
fix shape: commit first, or wait on the event). Recorded, not fixed here.

Residue stated in review and owed: the resume driver's wiring is now
run's, but a backend change across a restart is not part of the re-run
key (no "context identity"); the shell tool's hand-off file is not
reproduced by a re-run; a crash between the re-run and its commit
re-runs once more on resume (the rerun-then-commit seam); a tally inside
a diagnostic line reads as Fail (tally-first, by contract); the closure
executes workspace-controlled runners including `cd ..` — the same
contract as main, confinement is the container executor strand; no
fake-subprocess CLI composition test; the resolution-completeness check.

Patterns:

- **126. The driver's folded state is older than the driver.** `rs` is
  the run folded at START; this attempt's own invocations are not in it.
  Anything derived from what the attempt just did folds the journal
  again (`invoke.Fold(d.J.Production())`).
- **127. Tool bytes ride the evidence envelope.** A shell effect's
  input/output are stored byte-preserving under `{"role","b64","tool_call"}`;
  read them through `invoke.DecodeEvidence`, never as the raw thought.
- **128. Content checks before duplicate checks.** "Observed twice" and
  "already re-ran" fired before the lie was examined, so a lying record
  was refused for the wrong reason and the lying check was untested.
  Refuse a record for what it says first; duplicates last.
- **129. A recorded PATH never falls back to the host's.** A bare
  runner resolves through the command's own `PATH=` (relative from the
  dir; an EMPTY one is the dir), or not at all — a same-named host
  runner would answer for the one that is gone.
- **130. One classification, shared by the writer and the fold, total
  over every termination.** `Decide(family, exit, truncated, bytes)`:
  tally or exit says Fail everywhere; a flag (`why`, `truncated`,
  `timed_out`) never launders bytes; the fold enforces the writer's own
  invariants (a truncated capture kept `MaxCapture` bytes).
- **131. Kill the group and CHECK it.** `Setpgid`, cancel kills
  `-pgid`, and after the leader exits the group is killed again and
  the result waits (bounded) until nothing answers signal 0; a Pass
  over a group that did not exit is inconclusive.
- **132. Every entry point wires the backend the same way.** `runs
  resume` built a bare subprocess (no secrets drop, no ask file, the
  test timeout); the re-run then differed from the step's run by
  environment alone. Wire once, from one place, or the re-run is not a
  re-run.

Owed on this branch (unchanged unless noted): continuation (a rerun
claims and settles the run it continues — audit §3 item 2), the ask
grounding gate, work-dir binding, container executor + env-request
strand; the resolution-completeness check (a Resolution naming ALL
committed observations).

## Judgment — one typed seam, four providers, a shadow arm (2026-09-17, branch `jev`)

A judgment is now a typed question with a typed answer, not a prompt
string parsed by hope. `internal/judgment` carries the shape — Noul,
Choice and Score questions, answers with a distribution and a confidence
— and encodes it as TypeSafe's System One wire body exactly, so a
sidecar that mimics that shape works through the same code with nothing
added.

Providers are `invoke.Backend`s, which is the whole trick: the
invocation state machine, its receipts, usage accounting and the fold's
parity checks apply to a judgment call exactly as to any other, and a
new provider inherits all of it. Four are wired — `llm` (the incumbent
generative judge over the run's own backend, default), `hosted` (a cheap
OpenAI-compatible chat model behind the same prose template), `jev` and
`pcd` (System One over HTTP, tool-less, cannot act outward, key from the
secrets store by name and never printed).

The three judges — AGENDA step, AGENDA closure, NOW closure — express
their question as a Request instead of building their own prompt. One
renderer serves both the driver and the fold, so byte-for-byte
re-derivation cannot drift into two spellings; the prose template
carries a version and the parse is strict (a malformed answer is a
failed terminal, never a guess).

The shadow arm asks every configured provider the same question after
the primary answered, and commits a `shadow_judgment`. It cannot change
a verdict, and not by discipline: the record lands in the CONTROL
envelope and the resolver reads production only, which a scan asserts.
Default is empty — a second opinion costs money and reaches the network,
so it never turns itself on. `maro-go judgment report` renders the
agreement, the disagreements and the latency; `judgment replay` runs a
labelled corpus past any set of providers; an unreachable provider is a
skipped line, never a failed run.

- **133. A second opinion must be asked in its own name.** The first live
  shadowed run forwarded the primary's model into the wire body and the
  provider answered HTTP 400 for a model it does not serve. Recorded as a
  failed shadow with the run untouched — the arm working exactly as
  designed *and* a bug. The question, the state and the vocabulary
  travel; the model belongs to the provider.
- **134. A judgment's own defaults registry.** The Python
  `docs/DEFAULTS.md` census demands a reader in `src/` for every dotted
  key in a table row, so a Go key placed there fails the Python suite.
  The Go engine gets `go/DEFAULTS.md` plus `internal/defaults`, censused
  in both directions, and the Python doc points at it in prose. Two
  engines, two registries, one rule: OFF when it spends, ON when it only
  adds evidence.
- **135. Review r1 of the seam: the finding every seat found was the one
  the tests could not.** Four Codex seats converged on the same HIGH from
  independent probes: the AGENDA invocation closure sent every judge to
  the incumbent backend whatever `--judge-provider` said. The
  shadow-isolation test's adversary made the shadow path airtight and
  left the primary path with no non-default coverage. Fixed at the
  closure (a judge is asked through `d.primary(a)`), pinned by a run
  whose judge backend scripts only intent + plan. Same round: the
  resolved key is scrubbed from every byte a client hands the shell
  (`invoke.Redact`, not a "bearer " prefix match); a provider's timeout
  is a CEILING over the caller's twenty-minute budget and shadows ask
  under the registered minute; a fork child inherits the judgment
  binding through the one `childDriver` constructor; the decoder
  requires EOF, refuses duplicate keys and un-normalised distributions;
  the sidecar bounds its framing. Record:
  `docs/history/2026-09-17-judgment-providers-adversarial-review.md`.
- **136. A true finding can still be out of scope.** "Old journals no
  longer fold" was correct and was settled with binaries, not argument:
  the `successor` engine already refuses the live shadow-go journal at
  the intent prompt (84a7c12a changed it on 09-07 with no version
  dispatch). Journal↔template versioning is an engine gap, recorded as a
  lead, not a fix bolted onto this seam.
- **137. Three rounds, each one deeper into the fix layer.** r2 found the
  r1 scrub applied to one representation (bytes after the parse, the
  reason after the clip); r3 found the r2 scrub blind to a `\u`-escaped
  key and the r2 fold blind to Unicode (`ſcore` is `score` to
  `encoding/json`). The answer both times was to move the operation to
  where the meaning is: redact decoded string VALUES and re-encode
  (`invoke.RedactJSON`), fold by `unicode.SimpleFold` orbits (`foldKey`),
  refuse a key too short to be one before dispatch. Stopped at three by
  the round budget; every finding in r3 was in the r2 diff, none in the
  chunk under it.

## Post-v1 — continuation, Go side: a run that continues a stopped run claims it, and its own end settles it (2026-09-17)

**Ask (Jeremy, 2026-09-17, afk, the same order as the LoopsBench item 2 entry):** audit
§3 item 2. The Python main closed it 2026-09-16 (checkpoint chunks 6, 7
and 9: refusals end like finished runs, a resume CLAIMS its source before
executing, the run that ends a resume SETTLES its source). The Go engine
had the rerun relation and lineage, but a rerun re-planned and the prior
run was never told it was continued; two reruns of one stopped run were
two silent claims.

**Contract kept from main (audit §4.2):** the source is claimed durably
BEFORE anything executes; one continuation per source; a refused
continuation is recorded and the run ends on it like a finished run; the
source is settled by the continuation's end; a run that ends short keeps
the barrier.

**The Go engine's own road (D1, D5):**

- A run STOPS when its recorded outcome says it fell short: execution
  failed or partial, or closure `not_achieved`. Complete + `unknown` is
  FINISHED — the self claim cannot promote it (verdict rule 4) and
  nothing refuted it; a judge-less NOW run ends this way every time, so
  counting it as stopped would make every `--after` a claim and refuse
  the operator's second follow-up. `Stopped`, `stoppedOutcome`.
- `continuation/1` (run-scoped, attempt 0): goal, source, how
  (`after` = the operator named the run, which `answer` uses; `rerun` =
  the landscape chose it), `as_of` (the journal head the claim was decided
  over), `refused` (the exact state text when the claim was refused). The
  driver stage runs after the lineage/landscape and before attempt 1,
  idempotent by run (`continuation/<run>` key), crash seam
  `after_continuation`; the fold binds a run that died between claim and
  attempt 1 to its goal through the record.
- `Continuable(led, source, asOf)` is ONE decision shared by the CLI
  pre-checks (`--after`, `answer`: refuse before a goal is taken in), the
  driver (record the claim or its refusal) and the fold (the recorded
  refusal must equal the state as of `as_of`; a claim on a live /
  finished / already-continued source is refused; attempt 1 of a run
  following a stopped run without a record is refused once the journal
  shows the engine claims). States: not stopped ⇒ "has not stopped
  (attempt N state)"; continued ⇒ "is being continued by X (live)" /
  "was continued by X, which finished: follow X instead" / "which
  stopped: continue X instead".
- Settlement is DERIVED: `ContinuationState` = the continuation run's own
  outcome as of a head (live / finished / stopped). No settlement record.
  The chain moves forward: a stopped continuation is the thing to
  continue; there is no `--reclaim`.
- A refusal ends the run: `refusalOutcome` is the forced outcome `drive`
  records (attempt 1, or the attempt a resume makes — `ResumeRun` forces
  it ahead of the attempt bound); the fold requires any recorded outcome
  on a refused run to be `failed` with exactly `continuation refused:
  <text>`, and refuses that prefix on a run with no record (checked
  before the transition's own evidence, pattern 128).
- `ContinuationContext` renders the `## Continues prior run` block into
  `rs.Related` after the landscape's block (stop line; goal / answer /
  plan unless the rerun block carried them; where each step ended; the
  operator question); the fold re-derives it, so the execute request's
  hash pins it. `RelatedContext` now shares `planListing`.
- Surface: `asks --json` `follow_up` / `follow_up_state`; `runs show`
  "continued by X: state" / "continues X (how)" / "continuation refused:
  …"; `now --after` prints "continues: run X (stopped)" before intake.
- Registry: `continuation/1` declared + generated; `regression_rerun`'s
  `truncated` re-declared `authorization` (the fold has used it since r3
  of the entry above; measured by the truncated forge); `contracts report`
  0/0.

**Tests:** claim + derived settlement end to end (`--after` a failed NOW
run: record, lineage, the block in the execute request, `Continued`
index, "finished" after the continuation ends; the second `--after` of
the source refused by the pre-check and, driven anyway, recorded and
ended `failed` on the refusal with no block; a follow of the finished
continuation is a plain follow; a restart re-derives it all); refused
while the first is live and again after it finished; a not-yet-stopped
source; the chain moving forward (B stopped ⇒ "continue B instead"; D
continues B and carries B's stop line); the landscape's own rerun as a
continuation (`how: rerun`, one goal / one plan in the request, the
step-outcome line) and the judge choosing an already-continued source
ending refused; a `related` decision on a stopped run is no
continuation; kills at `after_continuation` (resume claims nothing
twice) and `after_landscape` on the rerun path (resume claims first);
nine forgeries, one history each (the honest refusal accepted as the
control; a claim on a continued source; a refusal that is not the
state; a source the run does not follow; how contradicting the lineage;
a claim on a finished run; decided after its own record; a claim after
the run started; attempt 1 without a claim); the outcome binding both
ways (a refused run recording another outcome; a refusal outcome on a
run with no record); wire table + JSON round trip + the door executing
the vocabulary; CLI: the ask loop's answer continues the asked run
(`asks --json`, both `runs show` lines, a second `--after` refused before
intake, no goal taken in).

**Review (codex gpt-5.6-sol, high; 2 rounds, per the 2026-09-16 budget
decree):** r1 = Skeptic + Architect on the chunk (10 + 9 findings, ~57KB
prompts, ~20 min each). Verified and fixed: two claims on one source
could both fold — a claim decided over head H never saw a claim appended
at H+1, and `continued[source]` was overwritten — and two drivers
deciding over the same head could both claim: now the driver folds a
reader pinned at the head it read and submits the record with
`ExpectHead` (re-deciding on the precondition, bounded), and the fold
requires `as_of + 1 == seq` ("decided over the head it was appended
to"); the same pin closes the refusal text describing post-head state
("attempt 1 executing" written after the source reached judged); the
ack transition (delivered→delivered) moved `TerminalAt` — now the FIRST
terminal transition; a second production run could start a goal another
run held (map-order source ambiguity) — the fold refuses it; a refused
continuation was itself continuable (its failed terminal read as
"stopped"), laundering the refusal — `Continuable` refuses a source that
ended on a refusal; `asks` hid `follow_up` unless an Answer existed;
`maro-go answer` left a run "already answered" with no follow-up when the
follow-up's own options were refused after the Answer commit — the same
answer resumes it; `runs show --json` lacked the continuation. Recorded
as intentional/residue: AGENDA step prompts do not carry the riders
(the planner consumes them; the plan is the executor's contract —
pre-existing, its own chunk); refusal authorization bound to the exact
text (deterministic; wording change = `continuation/2`); 8-hex handle
collisions and first-match `LineageOf` (pre-existing, every verb). r2 =
one Skeptic on the fix diff: 3 findings, all in the `answer` verb, all
fixed: the Answer was committed without a head precondition, so a
continuation claiming the source between the pre-check and the commit
stranded an answered-but-unfollowed run — the verb now decides over a
pinned prefix and appends the Answer with `ExpectHead` (re-deciding on
the precondition); the resume check missed a follow-up goal taken in
whose run had not started (`Ledger.Unstarted`) and would have taken in a
second child — now refused with the resume hint; the text `asks` still
printed "answer with:" for a run continued by hand without an Answer —
the follow-up is shown and the instruction suppressed. STOPPED after r2
per the budget. Residue: the Answer and the follow-up's claim are still
two commits (the claim is the follow-up driver's) — a claim landing
between them leaves the answer recorded and the follow-up ending refused,
honestly; a text fixture for the by-hand follow of an asked run.

Patterns:

- **133. "Stopped" is what the record says, not what it does not say.**
  Closure `unknown` after a complete execution is not a failure — the
  self claim cannot promote, but nothing refuted. A predicate that treats
  the absence of a judge as falling short turns every judge-less run into
  a claim target and refuses the operator's ordinary follow-ups.
- **134. One decision, three callers.** The CLI's pre-check, the
  driver's record and the fold's check all call `Continuable` over a
  ledger as of a head. A refusal text written twice would drift; the
  fold compares the recorded text to the function's own.
- **135. Settlement is the continuation's outcome.** A source's state is
  READ from the run that continued it, never written back. There is no
  settlement record to forget, no second writer, no "settled" flag that
  can disagree with the run it summarizes.
- **136. A forced outcome survives the resume.** Whatever forces a run to
  end (a refusal, the attempt bound) must be forced again by `ResumeRun`
  on the attempt the resume makes, or the resumed attempt executes the
  goal the run was refused for.
- **137. One history per forgery, even in one test.** A forged record the
  fold refuses stays in the journal; every later fold of that history
  refuses it first. A second forgery in the same harness is tested
  against the first's error, not its own (pattern 120, re-learned).
- **138. A decision over a prefix is appended to that prefix.** A claim
  that says "decided as of H" and lands at H+3 was not decided over the
  two records between; `as_of + 1 == seq`, and the writer commits with
  `ExpectHead` over a fold pinned at the head it read. Every "one per X"
  invariant decided from a fold needs both halves: the precondition (the
  writer) and the equality (the fold), or two writers deciding over the
  same head both win.
- **139. A terminal watermark is set once.** A later transition of the
  same terminal state (the ack) is not a second terminal; a watermark
  that moves makes every as-of question answered before it wrong.
- **140. A refusal is not work.** A run that ended on an authorization
  refusal reads as "stopped" to a predicate over outcomes, and the chain
  rule then lets it be continued — laundering the refusal into a fresh
  claim. Classify refusals apart from stops wherever "stopped" grants
  something.

Owed on this branch: landscape prompt v4 (show the judge which
candidates are already continued); the ask grounding gate (design
decision owed on the live ask); container executor + env-request
strand; the resolution-completeness check.

## Post-v1 — the work-dir binding, Go side: a continuation works where its source worked, and the config says so (2026-09-17)

**Ask (Jeremy, 2026-09-17, afk, the same order):** audit §3 item 4, the
port of main's landscape-binding edge (§2a). Main binds a continuing run
to the PROJECT of the run it continues (operator > landscape > named >
minted, stamped `project_binding`; 31 review rounds on the card/ledger
machinery around the decision). The Go engine had one `work/` for every
run, `--work` to point a run elsewhere, a `cwd` on every invocation that
nothing checked, and a resume that ran in the resuming process's default
whatever the original's `--work` was.

**Contract kept from main (audit §4.3):** a continuing run works where the
run it continues worked, and says so in a record the fold checks; the
operator's explicit choice overrides; the binding is decided once, at the
start, and does not drift between attempts.

**The Go engine's own road (D1, D5):**

- No projects: the work dir is the whole of it. `ConfigSnapshot.Work` +
  `WorkBinding` (`default` / `operator` / `continued`) on the attempt
  record — the config is where every other binding the fold checks
  already lives (lens, frame, backend, policy), so the binding is a field,
  not a record kind. `go/internal/run/work.go`.
- `Driver.Work` is the operator's dir; `Driver.WorkDefault` is the
  workspace's `work/` (fork children and replay arms inherit it as the
  default it is). `bindWork` decides at the run's first bound attempt:
  operator, else an unrefused continuation whose source recorded a dir
  (`rs.SourceWork` = `workOf(source)`: its first bound attempt's config,
  or for a run that predates the binding the one dir all its executes
  recorded), else the default. Later attempts repeat the first bound
  attempt's — the resume works where the run works; a migrated run
  adopts at its first bound attempt and holds.
- `Driver.work(cfg)` mkdirs the ATTEMPT's dir on request, never at
  binding time: a run that continues nothing and executes nothing
  creates nothing.
- Fold: `checkWorkBinding` on the attempt (continued names the source's
  dir exactly; default on a continuation whose source worked somewhere is
  refused — the override is `operator`, unverifiable and trusted as the
  operator's flag; a later attempt that moved from the first bound one is
  refused, and a first bound attempt after unbound ones is held to the
  meaning and to where the executes ran; an unbound config after the
  first bound one is refused — the continuation's watermark shape),
  `checkWork` on each invocation as it attaches (an execute ran exactly
  there; any other call that carries a cwd carries that one) — and the
  seam itself closed: an invocation that arrives before its attempt, or
  names attempt 0 of a run for anything but the landscape, is refused.
  The door refuses a relative dir, an unknown binding, `operator`/
  `continued` with no dir, a dir with no binding.
- Surface: `runs show` "works in <dir> (<binding>)", `--json`
  `work`/`work_binding`, event stage `work`.

**Review (codex gpt-5.6-sol, high; 3 rounds, per the 2026-09-16 budget
decree):** r1 = Skeptic + Architect on the chunk (8 + 8 findings, ~62KB
prompts, ~20 min each). Verified and fixed six: an invocation could fold
BEFORE its attempt and dodge every per-invocation rule (the lens and
backend rules had the same hole; the fold now refuses it); fork children
lost the parent's default dir; replay arms recorded `operator` for what
was the runner's default; a legacy source's recorded cwd was ignored
(the `workOf` derivation from its executes); a migrated run's attempt 2
skipped the binding's meaning; a vanished `continued` dir was re-created
empty under the old name. Three recorded intentional: `default` /
`operator` are self-attested (the engine's own writers), the watermark
instead of a `run_attempt/2` schema (chunk 2's road), lexical dir
identity. r2 = ONE Skeptic on the fix diff only: 3 findings, all
verified and fixed — two were regressions of the r1 fixes (the adopted
binding of a migrated run was read from attempt 1, so its next resume
could move it — `boundAttempt`, the run's FIRST bound attempt, now
answers everywhere; and a run-scoped attempt-0 invocation still dodged
the join — refused unless it is the landscape's) and one gap (the gone
dir was checked at attempt 1 only; now every attempt). r3 = the
regression round on the r2 → r3 fix diff: one LOW (the migrated-source
continuation had no end-to-end fixture — added: attempt 1 unbound,
attempt 2 adopts, the child under another default binds `continued` to
the adopted dir, executes there, refolds from disk), no regression.
Stopped at three per the decree. Full Go suite green three times
(before and after each fix set).

**Patterns (141+; the `jev` merge re-used 133–137 in its own entry above,
the continuation entry's 133–140 stand — numbering resumes here):**

- **141. One dir per run, answered by the first bound attempt.** A
  resume reads the run's binding, not the resuming process's default;
  and "the run's binding" is the first attempt that has one — attempt
  1 alone let a migrated run move on its next resume (review r2). Where
  the journal can predate a field, the reader of that field walks to
  the first record that carries it.
- **142. A call attaches to the attempt it names, or is refused.** The
  fold's per-invocation rules (lens, backend, work dir) execute when an
  invocation joins its attempt; an invocation that arrives before the
  attempt, or names attempt 0 of a run for anything but the landscape,
  joins nothing and used to be checked by nothing while an outcome could
  still cite it. Every seam a join crosses is a rule's blind spot until
  the join itself is a rule.
- **143. Derive the legacy fact from the record that existed then.** A
  run that predates the binding recorded its cwd on every execute; that
  is where it worked, and a continuation of it must go there — not to
  today's default because the new field is empty. When a field is new,
  the old runs are not "unknown"; they said it another way.
- **144. A continued dir is never created.** The dir is where the
  source's files are; a path that no longer exists is not that. mkdir
  would hand the continuation an empty dir under the old name and call
  it the source's. Stop before the call, say what is gone, resume after
  it is restored — for every attempt, not only the first.
- **145. A migrated run keeps what it adopts.** The first bound attempt
  of an old run is held to the binding's meaning AND to where the run's
  own executes already ran, and then answers for the run like attempt 1
  of a new one. Migration is a one-way door: the adoption is checked
  once and thereafter repeated, never re-decided.
- **146. One history per forgery, again (137).** The fixture that proves
  the driver keeps the adopted binding cannot share a journal with the
  refused forgery that proves the fold refuses a move — the refusal
  poisons the journal for the resume. Each claim about the driver gets
  its own journal.

**Owed:** a `--work` given to a run that dies before attempt 1 is lost on
resume (nothing records it before the attempt); a per-run work dir (the
default is still one shared `work/`); AGENDA judge calls drop the cwd
like NOW's (pre-existing); physical identity of a dir (a symlink
retargeted under a `continued` run is a move the fold cannot see); the
`run_attempt/2` road for the binding (a schema version instead of a
watermark) if mixed-version writers on one journal ever matter.

## Post-v1 — the ask grounding gate, Go side: a question is a claim, a failed claim comes back once as a record (2026-09-17)

**Ask (Jeremy, 2026-09-17, afk, the same order):** audit §3 item 3, the
portable half — Python main's grounding gate (`docs/OPERATOR_ASK_DESIGN.md`
§7, 2026-09-07: a worker asked the operator for "the 6-digit code" behind
a guessed link that 404'd, with no code sent; Jeremy: "I never received a
code"). Main probes the links, requires `sent` on a code request, bounces
the step once and passes a second failure through as `unverified`. The Go
engine read the ask file and sent whatever was in it.

**Contract kept from main (audit §4.4):** the same checks on the same
fields, the same one bounce with the same words to the worker, the same
pass-through as `unverified`, the same frame sentence.

**The Go engine's own road (D1, D5):**

- The bounce is a RECORD, `question_bounce/1`, committed before the
  re-run — because the fold re-derives every request: NOW's re-run must
  be byte-equal `Lensed(frame, goal + bounceBlock + riders + block)`, an
  AGENDA step's has the block after "## Your step". A bounce the fold
  could not see would make the re-run's request a forgery.
  `go/internal/run/ground.go`.
- A bounced call is consumed: recovery skips it, the fold refuses an
  Outcome or StepDone that cites it. A bounce is its attempt's: a crash
  after it resumes with a fresh call and no block.
- `Question` gains `invocation` (which execute wrote it; watermarked like
  the continuation and the work binding), `sent`, `unverified`. The lane
  rule — a code is consumed by the session that asked — cannot bounce
  here (pattern 109: the attempt ends on the question), so it rides as a
  soft `code_lane` note on every code request; the live window is the
  decision owed from Jeremy, not this chunk's.
- Fold: `checkQuestionBounce` / `checkQuestion` re-derive every problem
  from the ask itself (a link must be one of the ask's; `code_unsent` iff
  the ask asks for a code with no `sent` and is never left out; a bounced
  kind reaches a Question only after a bounce of that step; the lane note
  iff the question asks for a code). Door: subject run, invocation, ask,
  problems all present, bouncing checks only.
- Surface: `asks` and `runs show` print `sent` / `unverified` / "bounced
  once"; events `ask_bounced` then `ask` (`ask_stale_archived` when a
  fresh execute found a file it did not write).
- The frame reaches AGENDA (review r1): every AGENDA execute request
  begins with the attempt's frame, as NOW's does — the ask instructions
  live there, and an AGENDA worker had never seen them; the fold renders
  the producing attempt's frame into every step request. A resumed
  attempt runs under the run's frame when the resuming process has none.

**Review (decree 2026-09-16: 2–3 rounds, fix-diff-only re-reads):** r1 =
Skeptic + Architect (codex gpt-5.6-sol, high) on the whole chunk: 21
findings, 10 verified and fixed, the rest refuted, out of scope
(chunk 4's stated choices: judge cwd, `WorkOperator`), or intentional
(the five-link cap, Python's exact code regex — the intent's "4–8 digit"
was my wording, not the code's). Fixed: `maro-go resume` dropped the ask
path and ran the resumed worker under no frame (a resumed attempt now
runs under the RUN's frame — `inheritedFrame` — and the execute reads it
from the attempt config); NOW recovery ignored an ask left unread by a
crash after the receipt (the recovered call's file is grounded on the
resumed attempt: its question, or a bounce the resumed attempt commits
citing the recovered call, then one re-run); AGENDA recovery forgot a
committed question when the crash came after it (the step's judge, the
step itself) and went on with the plan (the resumed attempt ends on the
journal's question before anything else); a bounce or question could
name a step the attempt does not have, and a question after a bounce
could cite a call the bounce never reached (`stepOfAttempt`; the
re-run is this attempt's, Seq-after the bounce); an archive failure was
an event and a stale ask file was the next call's (archive-or-fail, and
every fresh execute archives what it finds first — `ask_stale_archived`);
NOW's usage saw only the re-run (both calls, in the outcome and the fold
rule); a problem's detail was trusted prose (the deterministic checks
are held to the gate's words, a link problem to naming the link); the
probe ignored the run's context (`ProbeURL(ctx, url)`, `httptest`
coverage of the real prober); AGENDA workers never saw the ask
instructions (every AGENDA execute request now begins with the
attempt's frame, rendered by the fold from the producing attempt's
config); fork children of a continued parent fell back to the driver's
default (chunk 4 residue: the parent run's dir is the child's default).
r2 = ONE Skeptic on the fix diff only: 5 findings, all verified
and fixed — two were regressions of the r1 AGENDA-recovery fix (it
replaced the inherited steps with the questioned attempt's, which the
fold does not; and it sat behind the attempt bound, so `MaxAttempts=1`
recorded "attempt bound reached" over the run's question), one a gap of
it (a pre-gate question with no invocation was skipped), one a gap of
the step-range rule ("no plan" read as NOW let a planless AGENDA attempt
carry step 0 — the rule now branches on the lane), one a LOW in the
archive's name probing (any stat error read as "occupied"). r3 = the
regression round on the r2 → r3 fix diff: 3 findings, all verified
and fixed (this is the STOP-RULE round: no r4). One HIGH: the recovered
pre-gate question's outcome cites no call, and the fold's invocation-less
branch checked only model and recall — its usage, steps and reason were
whatever the record said (an AGENDA outcome that recalled is now held to
the goal's cost and the steps it holds whether or not it cites a call;
one that did not recall accounts nothing; a response with no call is
refused; an attempt that asked records exactly `NeedsAnswer(question)`,
failed; a needs-answer reason must be a question the run asked and has
not recorded — mutation fixtures: invented/zeroed steps, invented/zeroed
usage, another question, the question in other words, a response with
no call, the asking attempt with another reason or a non-failed
terminal). Two MEDIUMs, both chunk-4 residue the gate's resume made
reachable: `workOf` read only executes, so a pre-binding AGENDA attempt
that died between its plan and its first execute was resumed in today's
default and its plan ran where it was not made (every call that carried
a cwd counts; disagreement is "" as before); an `operator`-bound dir
that was gone was re-created empty and the resume went on in it (a dir
the run has worked in must exist for a resume whatever bound it —
`workedIn`; a bound dir no call has run in is made as usual). Owed from
r3, not built: a forged legacy-AGENDA fixture that crashes after its
plan and resumes under another default (the `workOf` unit cases cover
the rule); a NOW-side "recovers ⇒ must end on it" fold rule (the AGENDA
driver guarantees it, the NOW recovery only when the asking attempt's
call is the one recovered).

**Patterns (147+):**

- **147. A bounce is a record because the re-run's request is derived
  from it.** The fold renders every request from the journal and refuses
  one that does not re-derive; a re-run whose prompt carries a bounce
  block the journal never saw is a forgery to the fold. Anything that
  changes a request is committed before the request is made.
- **148. A bounced call is consumed.** The execute whose worker wrote the
  failed ask landed, and a landed call is what recovery and the fold
  reach for; without the rule, a resume could cite it as the attempt's
  execute, or a StepDone could point at it. A call the gate rejected is
  spent — skipped on reuse, refused when cited.
- **149. A bounce is its attempt's.** Carrying bounce state across
  attempts would need a rule for which attempt's bounce a re-run answers
  to; scoping it to the attempt keeps resume a reuse of landed calls,
  at the price of one call after a crash between the bounce and the
  re-run. Pay the call; do not build the state.
- **150. On recovery, the question is the journal's, not the file's.**
  A resume that reused a landed execute re-read the ask file, found it
  archived, and concluded "no question" — a run that had asked ended as
  if it had not. The record is the fact; the file is what produced it.
- **151. A rule that cannot bounce rides as unverified.** Python's
  "a code request must be live" bounces because Python has a live lane;
  the Go lane ends the attempt on the question by design. Bouncing on it
  would bounce every code request forever. The rule becomes a note the
  operator reads, and the design decision stays visible as owed rather
  than papered over as a check that always passes.
- **152. Re-derive the problem from the ask, not from the check's word.**
  A `link` problem names a link the ask contains; `code_unsent` is true
  or false of the ask itself; the lane note is true iff the question asks
  for a code. A record that names a problem the ask cannot have, or
  leaves out one it must, is a forgery the fold can see without
  re-running the probe it cannot re-run (the network).
- **153. A file is the call's that ran while it was absent.** An ask file
  the driver finds is credited to the execute it just made — unless it
  was there before the call (a failed call wrote it; an archive never
  landed). Archive what is there before every fresh call; a call's
  claim starts from an empty inbox. And an archive that fails is a
  failure, not an event: the file is the record's input, and left in
  place it becomes the next call's.
- **154. The recovered call's file is grounded on the attempt that
  finds it.** A crash between the receipt and the read leaves the file
  where the call put it; the resumed attempt reads it as that call's
  and, if it must bounce, commits the bounce itself citing the recovered
  call — the bounce is attempt-scoped (149) AND may name an earlier
  attempt's call. The two rules compose: the re-run is always the
  bouncing attempt's.
- **155. The frame is the run's.** A resume that carried no frame ran
  the worker bare, and the AGENDA lane never had one: the ask
  instructions, the process's own "carry out this goal", were NOW-only
  and first-attempt-only. Every execute request of a run begins with the
  run's frame; the fold renders it from the attempt that made the call.
- **156. One history per forgery, again (137, 146).** The lane-note
  forgery and its honest twin share nothing: a refused Question poisons
  the journal for the honest one that follows.
- **157. An outcome that cites no call is still an accounting.** The
  fold held usage, steps and (never) the reason only under `if
  o.Invocation != ""`; the recovered pre-gate question's outcome had
  none, and a forgery could meter anything and route the tail with a
  reason nobody asked. Every Recorded outcome is re-derived — what it
  cites decides WHICH derivation, never whether there is one.
- **158. The record of where the run worked is every call that carried
  a cwd.** `workOf` read only executes because "the work happens in
  executes"; the planner's call ran in the dir too, and a run that died
  between plan and execute left only the planner's word. A derivation
  from the record must read the whole class that writes the field
  (watch-list probe 2, in the fold itself this time).

**Owed:** the live ask window (a window inside the attempt vs a
worker-side re-request rule — Jeremy's call; r1 added the consequence
that a bounce for an unrelated problem re-triggers a code delivery);
replay arms inherit the frame's ask path with no ask channel; the URL probe's SSRF
posture (same as Python's, unchanged); a failed step's ask is not read
(pre-existing); a crash after a bounce re-runs the step from scratch.

Owed on this branch: the live ask window (Jeremy's decision — a window
inside the attempt or a worker-side re-request rule); replay arms' ask
path; landscape prompt v4 (show the judge which candidates are already
continued); container executor + env-request strand; the
resolution-completeness check; the r3 residue above (a forged
legacy-AGENDA plan-then-crash fixture; a NOW "recovers ⇒ ends on it"
fold rule).


## Post-v1 — the container executor, Go side: where a call ran is part of its record, and the policy is the fold's (2026-09-18)

**Ask (Jeremy, 2026-09-17, the standing order):** audit §3's last item,
the container-executor + env-request strand. Python main has had a
containerized executor since 2026-07 and runs `require` on this box since
2026-09-13; the Go engine ran every worker on the host, with nothing in
the journal that even had a place to say otherwise. Two pieces of main's
history set the shape: on 2026-09-12 a degrade under mode `on` ran a
worker on the HOST and nothing in the record said so (the secrets store
was decrypted there), and main's container path cost 22 adversarial
rounds because it became a second execution path with its own capture
reader.

**Contract kept from main (audit §4.5):** the same three-value setting
(`off｜on｜require`), the same narrowing (tool-bearing calls only), the
same `docker run` shape (`--rm -i --init`, `--user uid:gid`, identity-
mapped binds, bare `-e NAME` with the value in the docker client's env),
and literally the same artifacts — image `maro-executor:2.1.210-r3`,
volume `maro-claude-auth`, HOME `/home/maro`, auth at
`/home/maro/.claude`. One built image, one login, two engines, like the
shared secrets store. The engine builds nothing: the operator runs main's
`container-setup` commands and the preflight names what is missing.

**The Go engine's own road (D1, D5):**

- **One launcher seam.** `invoke.Launcher` — `Executor()`,
  `Wrap(Launch) (Launched, error)`, `Preflight(ctx)` — with
  `HostLauncher` and `*Container` as its two implementations. The stream
  parser, the redaction, the secrets hand-off, the transcript capture and
  the terminal classification are the same code on both lanes; the only
  difference is the argv the child is started with. That is the answer to
  main's 22 rounds.
- **The executor is exposure, and it is decided ONCE.**
  `invoke.Executored.ExecutorFor(ctx, req)` is asked by the shell before
  the `invocation/1` record is written; the shell then commits the choice
  onto the invocation AND onto `Request.Executor`, and the launcher is a
  LOOKUP of what was committed rather than a second decision that could
  disagree with it. `invocation.executor = {kind, image, digest,
  network}`: the tag is what the operator named and can rebuild under,
  the digest (the daemon's `{{.Id}}`) is the world the call actually ran
  in, and the network is what it could reach. A backend that answers out
  of vocabulary is `ErrBackendContract`; an EMPTY kind means "this
  backend answers for none", never "the host".
- **The fold enforces the policy** (`run/executor.go:checkExecutor`,
  called beside `checkWork`): under `require` a tool-bearing call that
  ran on the host — or names nothing — is refused as the lie the policy
  exists to catch; under `on` a call that will not say WHERE it ran is
  refused too, because that is exactly the silent degrade of 2026-09-12;
  under `off` a container call is a call this engine would not have made.
  `ConfigSnapshot.Executor` records the policy per ATTEMPT (absent = off,
  which is what every earlier journal says), and the attempt's policy is
  read from the BACKEND — the one owner — through `invoke.Isolated`.
- **The narrowing is the DOOR's, not the fold's.** A tool-less call in a
  container is refused by `Invocation.ValidateWire`, so it holds for every
  record this engine will ever read — including the landscape call, which
  is attempt 0 and reaches no attempt's checks at all.
- **The degrade is derived, not trailed.** `run.ExecutorViewOf(a)` reads
  the attempt's own calls: the kind, the image, every distinct image the
  attempt used, and `degraded` iff the policy was `on` and a tool-bearing
  call ran on the host. The kind is EMPTY until a call has said where it
  ran, so a `require` attempt that never got as far as a call says
  "requires a container; no tool call has run yet" instead of claiming an
  isolation nothing exercised. The notice prints once per reason; the
  record is the evidence.
- **`require` fails closed before dispatch.** The preflight probes the
  daemon on EVERY call, then the image (capturing its digest), the volume,
  and the LOGIN inside it (`test -s <auth mount>/.credentials.json` — a
  volume that exists and holds no login is the failure an operator hits
  between `docker volume create` and `claude login`). Only successes are
  cached, so an operator who starts docker mid-run is picked up by the
  next attempt; the refusal is `ErrBeforeDispatch` with "…: <what>: <fix>
  and resume".
- **The launcher can END what it started.** Killing the `docker run`
  client does not kill the container — proved with a live probe on this
  box, where SIGKILL to the client left it `Up`. `Launched.Stop` runs
  `docker kill` on the container's own name (`maro-exec-go-<pid>-<n>`),
  and the subprocess lane defers it on a context error under
  `context.WithoutCancel`. The name keeps main's `maro-exec-` prefix on
  purpose: the stranded-container sweep already running on this box
  filters label + prefix, so a container this engine could not end is
  reaped by the sweep that exists.
- **What the worker writes crosses too.** `Launch.Writable` carries
  `$MARO_ASK` and the derived-secrets drop; the container binds each
  one's DIRECTORY read-write, identity-mapped. Never a file bind: it
  detaches on the atomic write-and-rename a careful writer does, and the
  worker's question would never leave the container. A writable path with
  no directory on the host is refused before dispatch instead of letting
  docker create a root-owned one. Each channel got its OWN directory
  (`drop/ask`, `drop/secrets`) so binding the one a step needs does not
  hand it every other channel's archive, and the answer-context file
  moved out of `drop/` into `context/` entirely.
- **What may never be bound.** `bindSource` resolves symlinks and refuses
  a source that IS or CONTAINS `/`, this engine's workspace, `$HOME`,
  `$HOME/.maro` or `$HOME/.maro-go`; a descendant is the normal case and
  is fine. Without it `--work <workspace>` would hand the orchestration
  to the worker read-write and still record "container".
- **An obligation is re-run where it was recorded** (`rerunWorld`):
  a container-recorded command comes back `regression.Inconclusive` with
  the reason instead of being re-run on the host, because that would be a
  different experiment wearing the same name and its green would retire
  an obligation nothing verified.
- **The same wiring on every command that runs work**: `now`, `agenda`,
  `runs resume`, `serve`, `experiment`, and `answer` passes the flags
  through to the run it resumes — a resume that wired the lane
  differently would run the rest of a required-container run on the host
  (pattern 132). The wiring is called even when a command has NO
  subprocess backend, so a policy nothing can keep is a typed error
  before the journal is touched rather than an attempt quietly recorded
  as `off`. And `--executor` with no value is an error: at the isolation
  boundary a missing value must not resolve to the least safe lane.
- **Proven on real docker**, not only in argv assertions:
  `TestContainerReallyRunsTheCall` runs the wrapped call on this box
  (gated on the real preflight, skipped elsewhere) and checks the three
  things argv cannot tell you — the worker's write reaches the host at
  the same path, the work dir is identity-mapped, and a secret named on
  the command line arrives inside the container with its value never in
  an argv.

**Review (decree 2026-09-16):** two rounds, both codex
gpt-5.6-sol at high effort, tree frozen while they ran.

**r1** (Skeptic + Architect over the whole chunk) found 7 distinct HIGH
and 8 MEDIUM. The two that mattered most were the same finding from both
lenses: a timed-out call killed only the `docker run` CLIENT (verified
with a live probe on this box — SIGKILL to the client left the container
`Up`), and the executor was decided TWICE, so the committed lane and the
lane that ran could disagree. Both were structural, not incidental: the
first added `Launched.Stop`, the second made the committed venue ride the
request. Two more were owner problems — `on` accepted a call that named no
venue at all, and the policy lived on the driver, the process options AND
the backend — fixed by deleting the other two owners. The tool-less
narrowing moved from the fold to the wire DOOR, because the landscape call
is attempt 0 and reaches no attempt's checks. The rest: the flags fail
closed on a missing value, every command that can run work is wired (a
policy nothing can keep is a typed error before the journal is touched),
`{kind, image}` grew a digest and a network, the preflight checks the
LOGIN and not just the volume, `--work` cannot bind a forbidden root, and
the view stops claiming a container before any call has run. The `drop/`
exposure was accepted in part (per-channel directories, answer context
moved out) with the residue recorded.

**r2** (one Skeptic, fix diff only) found 3 HIGH, 3 MEDIUM and 1 LOW, all
verified and all fixed — and every one of them was a hole IN a fix, which
is what a second round is for. The digest the fix added was recorded but
never used: the launch still named the mutable TAG, so an image rebuilt
between the preflight and the launch would run a world the record does not
name — now the launch is by the resolved id, and a launcher asked to run a
venue it does not have refuses before dispatch. The new kill path ran only
when the call's own context had been cancelled, which is exactly the case
the engine knows about: an independently killed client and a panic both
left a container running behind a context nobody cancelled, and the drop
file was ingested while the worker could still write — the stop now runs
on every way out, before the channels are read, and a container that could
not be ended downgrades the terminal to Partial rather than living only in
a notice. `answer`'s own passthrough loop still dropped a trailing
`--executor`: the round-1 defect surviving in the sibling parser. The
forbidden-root list could not express "the secrets store", because that
directory is a DESCENDANT of the forbidden `~/.maro` — a sealed-tree list
now refuses anything at or inside it, and the roots fail closed when they
cannot be resolved. The login probe was a container with no name, no label
and no kill path; `test -s` passed on a directory (the reviewer ran it);
and "every distinct image" only removed adjacent duplicates. **r3** (one Skeptic, the r2 fix diff only) found 2 HIGH and 2 MEDIUM, all
verified and all fixed, and all four were the same lesson in four places:
a check is only as good as the value it is given. The sealed-tree list
could not protect a RELATIVE `MARO_SECRETS_DIR` — `filepath.Rel` refuses a
mixed absolute/relative pair, so the containment test read "not contained"
for the very store it was protecting; the roots are now resolved to
absolute paths at the wiring edge, and a root that is still not absolute
refuses the bind instead of allowing it. The "usable image id" check
accepted any non-empty token, a TAG included, so the thing recorded as a
digest and handed to `docker run` could be mutable after all; it now has
to be `<algorithm>:<hex>`, and the login probe runs that pinned id too.
The failed-stop path changed the terminal label but still let `AfterTools`
read the worker's drop file — a container the engine could not end is a
worker that can still rewrite the channel being ingested, so the ingest is
now skipped and the reason says so. And the login probe's first-byte check
passed on `{}` and on any unrelated JSON: it now asks, with python3 inside
the same image, the question main asks — a `claudeAiOauth` object holding a
non-empty refresh token — proved against the live volume before it shipped.
Three rounds were budgeted and three were used; the stop rule ends it
here.

**Patterns**

- **159. The container is a launcher, not a backend.** One framer, one
  classifier, one capture reader; the lane difference is the argv. Main's
  other arrangement cost 22 rounds on a forked reader, and every one of
  them was about two code paths having to agree.
- **160. Where a call ran is part of its exposure.** Not metadata, not a
  log line: the same class of fact as the working directory and the tool
  policy, committed with the invocation BEFORE dispatch, answered from
  the same inputs as the dispatch itself. A decision made twice can
  disagree; a decision recorded once cannot.
- **161. A policy the fold enforces cannot degrade silently.** Main's
  2026-09-12 incident was not a missing feature, it was a record with
  nowhere to say what happened. Under `require` the fold refuses the
  attempt's own records, so the failure mode is a REFUSED run, not a
  quiet host execution.
- **162. An obligation is re-run where it was recorded, or not at all.**
  A probe that passed in a container and is re-run on the host is a
  different experiment wearing the same name. Inconclusive with a reason
  beats a green nothing verified.
- **163. Narrowing is a fact to check, not a habit to trust.** Tool-less
  calls run on the host because a verdict touches nothing — so the DOOR
  refuses a tool-less call that names a container (pattern 169). A
  narrowing that only lives in the dispatcher's head rots into a
  surprise.
- **164. A new lane carries everything the old one did, writes
  included.** The worker READS its secrets and WRITES its question: a
  lane that bound only the read paths would have taken the ask lane away
  from a containerized worker without a single failing test. And a
  single-file bind is not a bind — it detaches on the rename that every
  careful writer does.
- **165. Derive the surface from the calls, not from a second trail.**
  The degrade is a property of what the attempt's invocations say; a
  parallel event trail would be one more thing to keep honest, and (with
  forks) one more shared mutable thing to race on.
- **166. Cache the yes, re-probe the no — and never cache the liveness.**
  An image's digest and a volume's login are facts about the box that a
  successful probe settles; a FAILURE is exactly the thing an operator is
  off fixing, so caching it would make "start docker and resume" a lie.
  The daemon itself is neither: it can go away mid-run, so it is probed on
  every call and a cached success never speaks for it.
- **167. A setting is per attempt; a fact is per run.** The work dir
  cannot change under a run because it is where the files ARE (§4.3).
  The isolation policy can, because it is a decision about what to do
  next — and each attempt is held to the one its own config records.
- **168. A launcher that cannot end what it started has no timeout.**
  Killing the client is not killing the work: the `docker run` process
  dies and the container keeps going, holding the run's files and its
  secrets. Every lane that can start something must hand back the way to
  stop it, and the cancel path must run OUTSIDE the cancelled context.
- **169. A rule true of every record belongs at the door, not in a
  fold.** "A tool-less call is never containerized" was written as an
  attempt policy check, which the landscape call — attempt 0 — walks
  straight past. The door is the only place that sees everything.
- **170. At a safety boundary, a missing value is an error, not a
  default.** `--executor` with nothing after it resolved to the empty
  policy, which is `off`: one operator typo and the run silently took the
  least safe lane. Parse into (consumed, error) and refuse.
- **171. One setting, one owner.** The policy lived on the driver, on the
  process options AND on the backend; the recorded policy and the
  executed one could disagree. The owner is the thing that ENFORCES it —
  ask the backend, and delete the other two fields.
- **172. Launch the world the record names.** A tag is a mutable name for
  a world: an image rebuilt between the preflight and the launch runs
  something the record does not describe. Resolve the reference once,
  record the id, and hand the ID to the runtime — then the record and the
  call are one world by construction, and there is nothing to keep in
  agreement.
- **173. Clean up on every way out, not on the way you expected.** The
  first kill path ran only when the context had been cancelled — the
  deadline and the operator's ^C, the two cases the engine already knew
  about. An independently killed client and a panic both left a live
  container behind a context nobody cancelled. Stop unconditionally, once,
  under a context the cancellation cannot reach, BEFORE reading what the
  worker wrote. And every container the engine starts is one it must be
  able to end — the login probe included.
- **174. A contains-rule is not a containment rule.** "Never bind a path
  that IS or CONTAINS the workspace" has to allow descendants, because the
  drop directory is one. The secrets store is a descendant too, and walked
  straight through. A tree that must stay out needs its own list, and the
  list must fail closed when it cannot be resolved.
- **175. A predicate that passes on a directory proves nothing.** `test -s
  .credentials.json` was the login check; it passes on a directory, on an
  unreadable file, and on any non-empty garbage. Its replacement checked
  the first byte, which passes on `{}` — the same mistake one notch
  further in. State what the probe must prove, then ask for exactly that
  SHAPE (here: the CLI's own credential object with a non-empty refresh
  token, which is the question the other engine already asks in the same
  image) — and say out loud what it still does not prove (this one does
  not prove the session is unexpired).
- **176. A protection you cannot evaluate is a protection you do not
  have.** A relative root and an absolute bind source cannot be compared at
  all: `filepath.Rel` refuses the pair, and the containment test quietly
  answers "no". Resolve protection roots at the edge where the process's cwd
  still means something, and when a root is still unresolvable, refuse the
  operation rather than the protection.
- **177. Do not read what a lost worker can still write.** A container the
  engine could not end is a worker that may still rewrite the channel the
  engine is about to ingest. Reading a derived secret out of a call the
  engine has lost is worse than not reading one: skip the ingest, say so in
  the record, and leave the file for a later call on a sane box.
- **178. A reference that is not content-addressed is not a world.**
  "Not empty and no quotes in it" accepts a tag, and a tag recorded as a
  digest is the mutable reference the record was supposed to pin. Pin the
  FORMAT of an identity, not merely its presence — and pin every sibling
  that runs from it, the probes included.
