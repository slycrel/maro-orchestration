---
status: dormant-design
---

# Portable / Shareable Learning — Design + Migration Path

**Status:** Design (pre-implementation) — 1.0 arc item (g), BACKLOG.md:666.
**Decree:** Jeremy, 2026-07-09 (GOAL_BRAIN.md:1264): *"I think learning and
sharing needs to be part of the official first release."* … *"allowing for
machine migrations or data sharing to help bootstrap new users seems like a
no brainer down the road."* Internet hive-mind explicitly out of scope
(*"could be cool as an opt-in"*). Vision anchor: *"At the end of the day this
is sort of a communication platform after all, in addition to an action
generator."*
**Scope:** (A) machine migration — HDD dies, new box arrives, learning
survives; (B) bootstrap sharing — a curated pack one user hands another so a
fresh install doesn't start from zero. Not in scope: any always-on network
sync, central registry, or telemetry.

Provisional judgment calls Jeremy may want to overrule are tagged
**DECISION (provisional)** throughout; they are collected at the end.

---

## 0. The doors already built (design starts here, not from scratch)

| Door | Where | What it gives us |
|---|---|---|
| `maro-import` | `src/workspace_import.py` (entry point `pyproject.toml:51`) | Cross-workspace merge: runs copy-if-absent with `imported_from.json` provenance (workspace_import.py:79–86), ledger append with exact-line dedup under target locks (:107–117), daily-md marker-guarded append (:135–147), curated files quarantined to `imports/<label>/` never merged live (:19–22, :152–169), append-only audit in `memory/imports.jsonl` (:202–204). Proven live in the hermes trial 2026-07-09 (specimen: `~/claude/hermes-maro-trial/data/home/.maro/workspace`). |
| JSONL event log as source of truth | `src/memory_sqlite.py:1–36` header; `memory_events.jsonl` shared with adapter-0 (`src/memory_jsonl.py`) | The sqlite index is a **rebuildable cache**: schema-version mismatch or a shrunken/diverged log triggers full rebuild (memory_sqlite.py:130–131, 156–162). Stores are interchangeable on disk (contract-test proven). Portability = move the log; indexes take care of themselves. |
| Bi-temporal columns | `src/memory_sqlite.py` DDL: `created_at`/`expired_at` (transaction time) + `valid_at`/`invalid_at` (event time) | Exactly the distinction imports need: *when this store learned it* vs *when it became true*. |
| Decay trust, never data | `memory_port.MemoryStore.invalidate()` contract (memory_port.py:124–127); rule contestation (knowledge_lens.py:50–52: `contradictions ≥ 1 → contested: injected as verify-before-relying until refight_rule resolves it`) | The trust-demotion shape imported artifacts should arrive in already exists. Nothing new to invent — imports are contested-by-birth. |
| `secret_scrub` | `src/secret_scrub.py` — single function, deliberately one module so callers can't diverge (:1–8); already gates run recording (runs.py:394,398) and fixture harvesting (scripts/harvest_corpus.py:51) | The choke point ALL sharing must pass through. |
| Workspace → repo resolution order | `src/skill_loader.py:38–56` (workspace skills override), `src/persona.py:211–229` (workspace persona files shadow repo/`maro_assets` by stem); shipped defaults as `maro_assets` package data (skill_loader.py:40–46) | The collision-resolution prior art: local learning wins, shipped defaults refresh on upgrade. Imports slot in as a *third* origin that must not silently outrank either. |
| Stage-5 = regenerable-from-language | GOAL_BRAIN Decisions 2026-07-09, #8: Stage-5 rules are *"a compiled cache — portability comes via regeneration from language-form artifacts (skills/lessons/evidence), never from the .py itself"* | Settles what is NOT in a pack: compiled rules travel as the language that produced them. |

---

## 1. Inventory — what "learning" physically is today

Everything below lives under `~/.maro/workspace/` (repo CLAUDE.md, workspace
layout table). Classified by how it must travel:

**Class E — evidence (append-only, per-event):**
`runs/<id>/` dirs; `memory/*.jsonl` ledgers (outcomes, lessons, events,
captains_log, diagnoses, knowledge_nodes, knowledge_edges, calibration,
preflight_calibration, skill-stats, handle_inputs, persona-dispatch-log, …);
`memory/memory_events.jsonl` (memory-port log); daily `memory/<date>.md`.
Never rewritten; dedup by content is safe (workspace_import.py:107–111 relies
on this).

**Class C — compiled truth (rewrite-on-change, trust-bearing):**
`memory/standing_rules.jsonl` + `memory/hypotheses.jsonl`
(knowledge_lens.py:34–36; rewrite-on-change per memory_backends.py:18–19);
tiered lessons `memory/medium/` + `memory/long/` (knowledge_web.py:2–16,
decay/promote model :55–59); `memory/skills.jsonl` (Stage-4 extracted skill
records with `use_count`/`success_rate`); `playbook.md`; `MEMORY.md`. These
carry earned trust — the thing that must NOT travel at face value between
users.

**Class A — authored artifacts (files, name-keyed):**
`skills/*.md` (evolved skills, override shipped defaults), `personas/*.md`.
Trust is implicit in their existence (evolver wrote them because they won).

**Class R — regenerable caches (never packed):**
`index.db` (rebuilds from log, memory_sqlite.py:147–162); Stage-5 rule code
(GOAL_BRAIN #8); `correspondence.db` (dev-facing, re-ingestable);
FTS/ghost indexes generally.

**Class M — machine state (never learning, never shared):**
`~/.maro/config.yml` + `workspace/config.yml` (API keys, notify channels),
`secrets/`, `jobs.json`, task store, heartbeat state, `*.lock` files,
`telegram_offset.txt`. `maro-import` already refuses to touch these
(workspace_import.py:23–24) — keep that line absolute.

---

## 2. Q1 — The shareable unit

The two cases want **different units**, and conflating them is the main trap.

### 2a. Migration unit = the workspace itself

Same owner, same trust. The unit is `~/.maro/` wholesale — Classes E, C, A,
M all move; Class R rebuilds. No manifest, no scrub, no demotion. See §5.

### 2b. Sharing unit = the **learning pack** (curated, language-form)

**DECISION (provisional): a pack contains Classes C and A — compiled truth
and authored artifacts — and excludes raw runs (Class E) by default.**
Rationale:

- Raw runs are the largest privacy surface: `source/environment.json`
  captures hostname (runs.py:461), cwd, scrubbed-but-shaped env overrides
  and full config (runs.py:479–487); prompts/responses embed goal text that
  routinely names private context. Scrubbing catches secret-*shaped* strings
  only (§4).
- Evidence is local-context-bound: another user's box can't re-verify your
  run's claims, so the evidence value doesn't transfer — only the compiled
  lesson does. This is the crystallization thesis applied to sharing: you
  ship the tree rings, not the weather.
- `--include-runs <id>...` stays available as an explicit per-run opt-in
  (each named run passes the same scrub + review gate), because a worked
  example is sometimes the whole point of a share.

**Pack contents by artifact class:**

| In pack | Source | Travels as |
|---|---|---|
| Skills (authored) | `skills/*.md` | file, verbatim post-scrub |
| Skill records | `memory/skills.jsonl` rows | JSONL rows; `use_count`/`success_rate` preserved but relabeled as *claimed* stats (§3) |
| Personas | `personas/*.md` | file, verbatim post-scrub |
| Lessons | `memory/long/` (long tier only by default; `--include-medium` opt-in) | JSONL rows |
| Standing rules | `standing_rules.jsonl` | rows, **demoted to hypotheses on import** (§3) |
| Hypotheses | `hypotheses.jsonl` | rows |
| Knowledge nodes/edges | `knowledge_nodes.jsonl` / `knowledge_edges.jsonl` | opt-in (`--include-knowledge`) — most graph content is workspace-specific |
| Playbook | `playbook.md` | opt-in; quarantine-only on import (editorial, like maro-import's curated rule) |
| NOT in pack | Stage-5 rule code, indexes, runs (default), daily .md, MEMORY.md, anything Class M | — |

**Manifest format — `pack.json`, format v1:**

```json
{
  "pack_format": 1,
  "name": "polymarket-research-starter",
  "created_at": "2026-07-09T21:00:00+00:00",
  "origin": {
    "label": "user-chosen-string",
    "maro_version": "0.9.x",
    "scrubber_version": 1
  },
  "artifacts": [
    {"class": "skill_md",   "path": "artifacts/skills/edge-scan.md", "sha256": "..."},
    {"class": "lessons",    "path": "artifacts/memory/long/lessons.jsonl", "rows": 41, "sha256": "..."},
    {"class": "rules",      "path": "artifacts/memory/standing_rules.jsonl", "rows": 6, "sha256": "..."}
  ],
  "review": {"human_reviewed": true, "reviewed_at": "...", "review_manifest_sha256": "..."},
  "trust_policy": "demote-to-hypothesis"
}
```

Physical form: `<name>.maropack.tar.gz` = `pack.json` + `REVIEW.md` +
`artifacts/` mirroring workspace-relative paths. Mirroring the layout keeps
the importer trivially mappable and human-inspectable with `tar -t`.
**Deliberately no hostname/username/machine fingerprint in `origin`** — the
label is user-chosen prose; the manifest must not itself leak what the scrub
removed.

`artifact.class` is descriptive-not-a-gate, same posture as
`memory_port.KNOWN_KINDS` (memory_port.py:38–43): importers preserve/quarantine
unknown classes rather than failing (§6).

---

## 3. Q2 — Trust + provenance on import

Governing principle: **imports are contested-by-birth.** Same shape as rule
contestation (knowledge_lens.py:50–52) and "decay trust, never data"
(memory_port.py:26–29). Migration is the one exception: same owner ⇒ trust
preserved verbatim (a migration is a move, not a testimony).

### Provenance fields

- **memory-port items:** `MemoryItem.provenance` dict already exists
  (memory_port.py:57). Imports add keys:
  `{"imported_from": <label>, "pack": <name>+sha, "original_id": ...,
  "original_trust": <float>, "imported_at": <iso>}`. Existing provenance is
  never overwritten — nest under `imported.original_provenance` if present.
- **Runs:** existing `imported_from.json` marker (workspace_import.py:79–86)
  — unchanged.
- **Legacy ledger rows** (lessons, rules, hypotheses, skills.jsonl): add an
  `imported` object key to the row.
  **Implementation caveat (real bug waiting):** the rewrite-on-change stores
  round-trip through dataclasses whose `from_dict` filters to declared fields
  (`StandingRule.from_dict`, knowledge_lens.py:70–72; `Hypothesis.from_dict`,
  :96–101) — an `imported` key on a raw row is **silently dropped on the
  first rewrite**. The provenance stamp must be a declared field (additive,
  default `{}`/`""`) on `StandingRule`, `Hypothesis`, `TieredLesson`, and
  `Skill` before any pack import ships. Class-E append-only ledgers don't
  have this problem.

### Bi-temporal treatment

This is what the Graphiti-stolen columns are *for* (memory_sqlite.py:14–17):
on import, **event time** (`valid_at`) keeps the origin's timestamp (when it
became true), **transaction time** (`created_at`) is the import time (when
*this* store learned it). Recency/decay math runs on transaction time
locally, so a 3-month-old imported lesson isn't born half-decayed — it gets a
fair local hearing, then decays on local non-use like everything else.

### Arrival trust per class (sharing case)

| Class | Arrives as |
|---|---|
| Standing rules | **Demoted to `Hypothesis`** with `confirmations=0`, `contradictions=0`, `source_lesson_ids=["imported:<pack>/<rule_id>"]`. Earns rule status the normal way — `RULE_PROMOTE_CONFIRMATIONS = 2` (knowledge_lens.py:39). Exactly the contradiction-demotes-rule path, applied at the border. |
| Lessons | Enter **MEDIUM tier** regardless of origin tier, `sessions_validated=0`, score capped at **0.5**. Must earn LONG locally (promote at score ≥ 0.9 + 3 validated sessions, knowledge_web.py:57–58). Unreinforced, 0.5 decays under GC threshold 0.2 in ~6 days (0.85/day, knowledge_web.py:55,59) — an ignored import self-composts. That is decay-trust-never-data doing the quarantine for free. |
| memory-port items | `trust = min(original_trust, 0.5)`, provenance stamped. Below full trust, above `MIN_RECALL_TRUST = 0.2` (memory_port.py:48) — retrievable but outranked by anything locally earned (rank = relevance × trust in both adapters). |
| Skill records (skills.jsonl) | Imported with stats moved to `imported.claimed_use_count` / `claimed_success_rate`; local counters start at 0. Local `success_rate` is what promotion/demotion sweeps read (skill_lifecycle.py maintenance) — claimed stats are display/tiebreak context only. |
| Skills/personas (.md) | **Never land directly in `skills/`/`personas/`.** Quarantined to `imports/<label>/` (existing mechanism, workspace_import.py:152–169) + explicit adoption step (below). |

**DECISION (provisional): 0.5 trust cap / MEDIUM-tier entry** are the
concrete numbers; the *shape* (contested-by-birth, earn locally, decay if
ignored) is the decision that matters. Numbers can move after live evidence.

### Collisions with local learning

Resolution-order prior art: workspace → repo, workspace wins (CLAUDE.md;
skill_loader.py:38–56, persona.py:211–229). Imports must not silently join
that ladder. Rules:

1. **Content-hash identical** → skip (already-known; mirrors the exact-line
   dedup of workspace_import.py:107–111). Optionally count as one
   confirmation of the local copy — **DECISION (provisional): no** for v1;
   cross-user confirmation-inflation is hive-mind-shaped and out of scope.
2. **Same name, different content (skills/personas)** → local wins, import
   stays in quarantine with a `CONFLICTS.md` note. Never overwrite; adoption
   is editorial (workspace_import.py:19–22 posture).
3. **Semantic near-duplicates (lessons/rules)** → no special handling in v1.
   Both live; the existing reinforcement/similarity machinery
   (`_text_similarity`, reinforcement bump) converges them the same way two
   locally-learned near-dupes converge.

### Adoption — the explicit gate from quarantine to live

New verb: `maro-pack adopt <label> [skill-or-persona ...]` — copies from
`imports/<label>/` into `workspace/skills|personas/` with a provenance
header stamped into the file's frontmatter, records the act in
`imports.jsonl`. This keeps the gardener role intact (KNOWLEDGE_CRYSTALLIZATION.md
"The Gardener Role"): the system surfaces, the human promotes. For the
bootstrap-a-new-user case the friction is one command
(`maro-pack adopt <label> --all`), which is the right amount of friction for
"run someone else's code-shaped instructions."

### How contestation resolves

No new machinery. Imported hypotheses/lessons ride the existing loops:
confirmations via `observe_pattern`/reinforcement (knowledge_web.py:285–301),
contradiction via the existing contested path, rule re-fights via
`refight_rule` in the maintenance sweep (skill_lifecycle.py run_skill_maintenance),
decay via `run_decay_cycle` (knowledge_web.py:596+). The border demotes;
the interior already knows how to adjudicate.

---

## 4. Q3 — Privacy scrubbing guarantees

### What `secret_scrub` covers today (secret_scrub.py:13–20)

Six regexes, recursively over any JSON-ish value: Anthropic keys (`sk-ant-…`),
generic `sk-…` keys, GitHub tokens (`gh[pousr]_…`), Slack tokens (`xox[baprs]-…`),
AWS access-key ids (`AKIA…`), and generic
`bearer|authorization|api[_-]?key|token|secret|password ∶= <8+ chars>`
assignments. Conservative by design (:6–7). It covers **secret-shaped
strings**, nothing else.

### What sharing additionally exposes (and regex cannot decide)

- Absolute paths and the username inside them (`/home/clawd/...` — in skill
  bodies, lesson text, playbook lines).
- Hostnames (captured verbatim into run environment.json, runs.py:461; also
  free-floating in lesson prose).
- Email addresses, Telegram handles, repo names of private repos.
- **Goal and lesson text itself** — "learned while researching Jeremy's tax
  situation" is not secret-shaped and is the worst leak in the list. No
  mechanical pass can rule on semantics.

### The guarantee — stated honestly

**DECISION (provisional): the sharing guarantee is "mechanical scrub for
secret-shaped strings + mechanical redaction of known local identifiers +
a mandatory human review gate." We do not claim mechanical anonymization,
and the docs must not imply it.** A pack is a *letter* (the communication-
platform framing) — you proofread letters.

Concretely:

1. **Keep the single-source rule.** Extend `secret_scrub.py` (same module,
   so paths can never diverge — its founding constraint, :5–7) with a second
   function `scrub_identifiers(obj)` that redacts: `$HOME` prefix + username,
   `platform.node()` hostname, configured email/handles (read from a small
   deny-list the exporter assembles from config + environment, never
   hardcoded). Replacement tokens are stable (`[HOME]`, `[HOST]`) so skills
   remain executable-in-spirit on the receiving side.
2. **Export flow: export → scrub → human review → seal.**
   - `maro-pack export` gathers artifacts, applies `scrub()` +
     `scrub_identifiers()` to every string.
   - Writes `REVIEW.md`: one section per artifact, full post-scrub text of
     every lesson/rule/hypothesis row and skill/persona file, with lines that
     *were* redacted highlighted — the human reads what will actually ship.
   - The pack is written **unsealed** (`review.human_reviewed: false`).
     `maro-pack seal` (or `export --seal` after an interactive confirmation)
     stamps `human_reviewed: true` + `reviewed_at` + the sha256 of the
     reviewed `REVIEW.md`, so post-review tampering is detectable.
   - `maro-pack import` **refuses unsealed packs** by default;
     `--allow-unreviewed` exists for self-to-self transfers.
3. **Migration is exempt** from scrubbing entirely (same owner, secrets are
   *supposed* to move). The migration runbook says "this tarball contains
   your API keys — move it over ssh/scp, then shred it," and that's the whole
   privacy story for case A.

---

## 5. The migration path (case A)

Two sub-cases with different right answers:

### 5a. Migration to an EMPTY new machine (the HDD-died case)

Plain copy is correct and complete — no merge semantics needed:

```
old$ tar czf maro-backup.tgz -C ~ .maro
new$ pip install maro-orchestration && tar xzf maro-backup.tgz -C ~
new$ maro-doctor
```

What just worked, and why:
- All learning (Classes E, C, A) is files under `~/.maro/` — no external DB.
- `index.db` needs nothing: on first open the store detects the copied log
  and catches up / rebuilds from `memory_events.jsonl`
  (memory_sqlite.py:130–131, 156–162). If paranoid, delete it; rebuild is the
  designed path.
- Shipped skills/personas come from the new install's `maro_assets`
  package data (skill_loader.py:40–46, persona.py:196–201); workspace
  overrides in the tarball keep winning by the resolution order. Upgrading
  Maro during migration therefore refreshes defaults *without* touching
  evolved artifacts — the resolution order is doing the migration-compat
  work for free.
- Config moves verbatim (both tiers, CLAUDE.md config table).

Gaps `maro-doctor` should learn to check post-migration (the actual work in
this slice):
- absolute paths burned into `config.yml` values that don't exist on the new
  box;
- stale machine state that traveled: `jobs.json`, heartbeat state, `*.lock`
  files, `telegram_offset.txt`. **Recommendation: doctor flags these and the
  runbook says delete them — schedules and heartbeats must be re-armed
  intentionally on the new box, never auto-revived by a restore.** (The
  good-system-citizen rule: off switches stay off; a backup that re-arms a
  heartbeat is a self-rearming loop with extra steps.)
- index/log divergence (already self-healing, but doctor should say so
  rather than leave the user wondering).

### 5b. Migration INTO an existing workspace (consolidating two installs)

This is exactly `maro-import` and it already works (hermes trial,
2026-07-09): runs, ledgers, daily logs merge with dedup + provenance;
curated state quarantines with `--include-curated`. Two gaps:

- **Adoption:** curated artifacts stop at quarantine. `maro-pack adopt` (§3)
  closes this for both import paths at once.
- **`memory_events.jsonl` coverage:** already matched by the
  `memory/**/*.jsonl` ledger glob (workspace_import.py:43,96) — exact-line
  append-dedup is *correct* for an event log (append-only, id-carrying
  events; replay handles order). No change needed, but the design notes it
  as load-bearing: if the memory-port log ever moves out of `memory/`,
  maro-import must follow.
- **Trust on 5b merges:** same owner ⇒ no demotion. `maro-import` stays
  trust-neutral; only `maro-pack import` demotes.

---

## 6. Q4 — Format versioning

- `pack_format: 1`, a single integer in `pack.json`. Bump on semantic change
  (a field means something different, a class's import semantics change);
  additive fields do NOT bump — importers ignore unknown manifest keys.
- **Older pack, newer Maro:** per-version upgrade shims at import time
  (`_upgrade_1_to_2(manifest)` chain) — same posture as the sqlite
  `schema_meta` version check that triggers rebuild-not-wrongness
  (memory_sqlite.py:17–20, 130–131). Old packs keep importing forever;
  shims are cheap because the payload is JSONL + md.
- **Newer pack, older Maro:** refuse with an actionable message ("pack
  format 3 > supported 2 — upgrade maro"), payload-first, no traceback.
  Never best-effort a newer format: silent partial import of trust-bearing
  data is the one unrecoverable failure here.
- **Unknown artifact classes** (same-format additive growth): quarantine to
  `imports/<label>/unknown/` + list in the import report; never fail the
  import. Mirrors `KNOWN_KINDS` descriptive-not-gate (memory_port.py:38–39)
  and the corrupt-line posture of the event log replay ("one bad line loses
  one event, never the store", memory_jsonl.py:92–95).
- **Row-level compat is already solved** by the dataclass `from_dict`
  filters (unknown keys dropped on load) — with the §3 caveat that this
  same filter *eats* provenance until the fields are declared.
- Migration tarballs (§5a) are deliberately **unversioned** — they are the
  workspace itself, and workspace-format evolution is owned by the code that
  reads it (the sqlite rebuild pattern), not by an export format.

---

## 7. Implementation slicing (one session each)

Ordered; each chunk lands whole with tests.

1. **Migration runbook + doctor checks** — `docs/MIGRATION.md` (5a + 5b
   procedures verbatim from §5) + `maro-doctor` post-migration checks
   (stale machine state, burned-in paths, index self-heal confirmation).
   No new subsystem; highest user value per line. *The HDD-dies story is
   fully closed after this chunk.*
2. **Provenance fields** — declare `imported`/provenance fields on
   `StandingRule`, `Hypothesis`, `TieredLesson`, `Skill` (the §3 rewrite-
   eats-unknown-keys caveat) + `scrub_identifiers()` in `secret_scrub.py`.
   Pure additive, unblocks both following chunks.
3. **`maro-pack export` + `seal`** — pack.json v1, artifact gathering
   (Class C + A defaults, opt-ins), scrub pipeline, `REVIEW.md`, seal
   flow. Sharing is *produce-only* after this chunk — already useful
   (publish a starter pack for the 1.0 default-content work, item (e)).
4. **`maro-pack import` + `adopt`** — seal check, format check + shim seam,
   trust demotion per §3 table, collision rules, quarantine + adopt,
   imports.jsonl audit rows. Closes the loop.
5. **Post-1.0:** pack signing/identity, richer identifier scrub, imported-
   skill A/B before adoption, confirmation-sharing semantics, and the
   opt-in hive-mind — explicitly deferred by decree.

**Minimum 1.0 slice = chunks 1–4.** If squeezed, chunks 1–3: migration is
non-negotiable (data loss story), and an export-only release still delivers
"sharing" (packs can be produced and hand-imported next release), but 4 is
small once 2–3 exist and the decree says learning *and sharing* — recommend
all four.

New CLI surface: **`maro-pack`** (export/seal/import/adopt/inspect).
**DECISION (provisional):** keep `maro-import` untouched as the trust-neutral
workspace-merge tool; `maro-pack` owns the trust-demoting curated lifecycle.
Two tools because they answer different trust questions; sharing semantics
bolted onto `maro-import` flags would make its migration behavior
mode-dependent — exactly the config-conditional shape CODING_NOTES warns
about. Any new default this introduces (trust cap, seal requirement)
registers in `docs/DEFAULTS.md` per the defaults-registry decree.

---

## 8. DECISION (provisional) — collected for review

> **RATIFIED 2026-07-12 (Jeremy, all 8 as written — GOAL_BRAIN Decisions
> 2026-07-12).** Numbers stay tunable; the shape is the commitment.
> Implementation (§7 chunks 1–4) is unblocked and queued in MILESTONES.

1. **§2b — Packs exclude raw runs by default** (`--include-runs <id>` opt-in
   with per-run review). Privacy surface + evidence doesn't transfer.
2. **§3 — Arrival-trust numbers:** rules→hypotheses at 0 confirmations;
   lessons→MEDIUM at score 0.5, `sessions_validated=0`; port items
   trust-capped at 0.5. Shape is the commitment; numbers are tunable.
3. **§3 — Content-hash-identical import does NOT confirm the local copy**
   (cross-user confirmation inflation is hive-mind-shaped; out of scope v1).
4. **§3 — Skills/personas never auto-adopt**; quarantine + `maro-pack adopt`
   even for bootstrap users (one `--all` command of friction).
5. **§4 — Guarantee wording:** mechanical for secret-shaped + identifier
   deny-list + mandatory human review gate; unsealed packs refused on import
   by default. No anonymization claim.
6. **§5a — Restored machine state (heartbeat, jobs.json, locks) is flagged
   for deletion, never auto-revived.**
7. **§7 — `maro-pack` is a new tool; `maro-import` stays trust-neutral and
   unchanged.**
8. **§7 — Minimum 1.0 slice = chunks 1–4** (migration + full pack
   export/import); export-only (1–3) is the named fallback.
