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
