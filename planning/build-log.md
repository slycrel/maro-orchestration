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
