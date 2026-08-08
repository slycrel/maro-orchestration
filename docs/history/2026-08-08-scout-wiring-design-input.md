---
status: record
---

# Scout wiring — design input

**Item:** BACKLOG "Poe's X steal-list run (2026-08-05, handle `83a2c805`)" §1,
*STEAL — scout wiring (its T1/T4/S1, post-correction)*.

**Method:** read-only against `origin/main` @ `d7a193d`, plus a read-only
census over the box's 773 run dirs and a live replay of the skill matcher.
Nothing written, nothing dispatched. Caller censuses were re-run rather than
inherited — which is how two of the four legs turned out to be mis-filed.

---

## Verdict up front

The item is filed as a wiring job between three existing parts. It is not one.
And the deeper problem is that **its premise is empirically empty**:

> "Derive queries from maro's own skill gaps" has no corpus to derive from.
> Across all 94 post-ship runs that recorded a skills manifest, **zero**
> registered an empty skill injection. The gap signal maro has is binary —
> *"nothing matched at all"* — and on a warm 376-skill store that condition
> essentially never occurs.

This is not a bug. The matcher is doing genuine work (93% of runs match on real
trigger patterns, and out-of-domain goals correctly match nothing). The signal
is simply **the wrong shape**: a scout needs to know *where matches are weak*,
and nothing anywhere records that.

Two consequences fall out, both independent of whether the scout is ever built:

1. **Phase-32 skill synthesis is de facto dormant.** Shipped `e315502`
   2026-03-27, it fires only on an empty match set — which the corpus says
   never happens.
2. **The cheap, useful slice is not a gap census — it is match-tier
   telemetry.** Detail below.

---

## Leg-by-leg

| Leg (as filed) | Status |
|---|---|
| GitHub-wide search exists, zero autonomous callers | ✅ accurate (one nuance) |
| "derives queries from maro's own skill gaps" | ❌ **premise empty** — signal exists, never fires |
| "→ existing `repo_scan.py`" | ❌ wrong component |
| "→ `skills.py:extract_skills` (input-shape-ready)" | ❌ stated backwards |

### Leg 1 — GitHub search: accurate, with a nuance that reframes ROI

`channels.GitHubChannel.search_repositories` (`src/channels.py:95`),
`search_code` (`:127`), `search_issues` (`:153`), fronted by `github_search`
(`:366`). Outside `channels.py` and its tests the only references are
`fetch_tool.py`'s dispatch (`src/fetch_tool.py:85-89`), exposing them as fetch
modes `github_repos` / `github_code` / `github_issues`. "Zero autonomous
callers" holds.

**The nuance:** the capability is already *agent-reachable*. A worker that
decides mid-run to go look at GitHub can, today. What is missing is a trigger
and a query — not reach. So the scout is not unlocking a capability; it is
choosing to spend tokens on one nobody currently chooses to spend. The ROI
question is "does an unprompted scout beat a worker who asks when it needs
to?", which is a measurement, not an argument.

### Leg 2 — the gap signal exists, has a consumer, and never fires

`had_no_matching_skill` (`src/loop_types.py:217`) — its own comment names the
purpose: *"the synthesis trigger."* Set at `loop_planning.py:509`
(`not _matched_and_routed`) and `:511` (fail-closed), cleared at `:548` if the
curated loader matched, threaded via `agent_loop.py:610`, consumed at
**`loop_finalize.py:919`** → `evolver.synthesize_skill` (Phase 32, `e315502`,
2026-03-27).

**It is not persisted.** 0 of 1,496 rows in the box's `outcomes.jsonl` carry
the field; 0 of 773 `metadata.json` files mention it. It is an in-memory
`LoopResult` field consumed at finalize and discarded. The only durable trace
of the condition is `skills_manifest.jsonl` present-and-empty
(`runs.append_skills_manifest`, `src/runs.py:1147`), whose docstring is
explicit that empty-vs-absent was made distinguishable on purpose.

**Census (box, read-only, all 773 run dirs):**

| | |
|---|---|
| run dirs | 773 |
| with a `skills_manifest.jsonl` | 94 (manifest ships 2026-07-09, `f49f318`) |
| manifest date range | 2026-07-09 → 2026-08-08 |
| stages recorded | `decompose` 94 runs, `curated_summaries` 77 runs |
| **decompose-site misses (the gap condition)** | **0** |

Zero. Split by stage, so this is not the two-injection-site conflation
masking it.

### Why it never fires — and why that is *not* a defect

`find_matching_skills` (`src/skills.py:616`) is three tiers:

1. **Router** (`route_skills`, `src/router.py:353`) — returns top-3 by
   predicted success. Live-tested on the box against four goals: **all four
   returned `method='keyword'`**, i.e. the router's scores were
   non-discriminating and the `DISCRIMINATION_EPSILON` (0.05) guard correctly
   fell through. Worth noting because the router's docstring admits its model
   is trained on *skill text only*, so its scores are "a per-skill prior,
   identical for every goal" — a real goal-insensitivity hazard that the
   epsilon guard is currently, correctly, suppressing. **Not live today; worth
   a tripwire if the model is ever retrained into discriminating range.**
2. **Keyword** — substring overlap on `trigger_patterns`, threshold `score > 0`.
3. **TF-IDF fallback** (`_tfidf_skill_rank`, `skills.py:358`) — cosine, keeps
   only non-zero similarity, top-2.

**Replay over the 94 manifest runs** (their real prompts, against today's
376-skill store):

| tier that supplied the match | runs | share |
|---|---|---|
| genuine trigger-pattern match | 87 | **93%** |
| TF-IDF fallback only | 7 | 7% |
| nothing at all | 0 | 0% |

And the fallback tier is **not** returning junk. Control probes:

| probe | result |
|---|---|
| `"bake a sourdough loaf"` | **None** |
| `"translate this to Klingon"` | **None** |
| `"asdfqwer zzzz xyzzy"` | **None** |
| `"deploy kubernetes to staging"` | `Staged Research Artifact Pipeline` — 1 shared token (`stage`) |

The seven live fallbacks all won on **2–3 shared tokens**, and read as weakly
but genuinely related (`"non-ethanol gas near Manti, Utah"` →
`Location-based Resource Research`). The only bad shape found is the
single-shared-token collision the kubernetes probe produces, and **it does not
occur in the live corpus**.

*(A note against myself: my first "nonsense" probe was
`"asdfqwer zzzz nonsense tokens xyzzy"`, which returned two skills — but it
contains `tokens`, a real domain term. It was never a nonsense probe. The
corrected controls above are the ones that mean anything. Recorded because the
same mistake would recur.)*

**Conclusion:** the matcher is healthy. The gap signal never fires because on
this workload there genuinely are no *total* misses — not because the signal is
broken. A binary "nothing matched" flag cannot express what a scout needs.

### Leg 3 — `repo_scan.py` is the wrong component

`scan_repo(repo_path, *, max_depth=2) -> RepoStack` (`src/repo_scan.py:158`)
walks a **local directory** and returns languages / frameworks / test
frameworks / tags. Sole caller: `loop_planning.py:591`, describing *the repo the
run is already in*. Two independent mismatches:

1. **No fetch→disk bridge exists.** `scan_repo` takes a path; the GitHub
   channel returns API JSON. Bridging means cloning third-party repos, which
   reopens the untrusted-git boundary C3 already litigated
   (`_sanitize_untrusted_git` — never run plain `git` against world-controlled
   checkouts).
2. **Wrong output kind.** A `RepoStack` says "python, pytest, fastapi." It
   cannot answer *how* anyone does anything.

The likely cause of the mis-file: `repo_scan.py:336` defines
`find_skills_for_stack(stack, skills)` — which matches **existing** skills to a
stack. That is the scout's inverse, and it is the only skill-adjacent function
in the module.

### Leg 4 — `extract_skills` is the opposite of input-shape-ready

`extract_skills(outcomes: List[dict], adapter)` (`src/skills.py:238`) consumes
**maro's own run outcomes**:

- filters through `outcome_policy.is_learnable_outcome` (`:258`), which demands
  a recognized `success_class` or `goal_achieved is True`;
- with nothing surviving, **returns `[]` at `:262` — before any LLM call**. Fed
  repo content it is a silent no-op;
- its payload is literally `"Successful goal completions to analyze"` (`:283`);
- it stamps `source_loop_ids` (`:303`) — maro loop provenance by construction.

The one place that hand-builds its input (`loop_finalize.py:897-907`) assembles
it **from a real run**. Making external content reach it means forging outcome
dicts with a `goal_achieved` / `outcome_id` no run produced — fabricated
provenance, the exact shape `src/lesson_provenance.py` (2026-07-29,
`minted_from`) exists to stop.

**The BACKLOG entry's "input-shape-ready" parenthetical is wrong and should be
corrected regardless of what happens to this item.** It originated in run
`83a2c805` and does not survive reading the signature.

---

## Recommendation — replace the gap census with match-tier telemetry

The aggregation slice as originally scoped (roll up `had_no_matching_skill`)
would return an empty list, because the condition never occurs. The useful
version is one step sideways and about as cheap:

> **Record *how* each skill was selected, not just *which*.**
> `append_skills_manifest` entries already carry `id` / `name` /
> `content_hash` / `variant_of` / `tier` / `routing_key`. Add the **match tier**
> (`router` | `keyword` | `tfidf_fallback`) and its **score/overlap**.

That converts a binary flag that never fires into a graded signal that fires on
every run, and it is a few lines at the two injection sites in
`loop_planning.py`. With it you get, for free:

- **A real gap corpus** — "goals whose best match was a weak fallback" is the
  query source the scout actually needs, and today it is 7% of runs rather
  than 0%.
- **A live trigger for Phase-32 synthesis**, which has been dormant since
  2026-03-27 and would finally have a condition that occurs.
- **Honest skill attribution.** Utility scores, A/B `variant_wins` and
  `record_skill_outcome` currently credit trigger matches and weak fallbacks
  identically. 7% of injections are a different kind of event and are not
  labelled as one.
- **The floor question, measurable.** A floor of ≥2 shared tokens would
  preserve all 7 live fallbacks and eliminate the 1-token collision class. That
  is a data-derived number, not a guessed one — but it should be set from the
  logged distribution once the telemetry exists, per the standing posture that
  a numeric cut on evidence needs a measurement rather than a default. **Do not
  ship a floor before the telemetry.**

Only after that does the scout's premise become measurable — and the 7% figure
is what should decide whether it is worth building at all.

### Caveat on the replay

The 93/7/0 split replays historical prompts against **today's** 376-skill
store, not the store as it stood at each run. The store grew over the window,
so today's store makes matches *more* likely; the true historical fallback rate
could be higher. The **0 misses** figure does not depend on the replay — it is
read from the live manifests as recorded at the time, and it is the load-bearing
number here.

---

## Decisions this needs

1. **Does a world-sourced skill ever get written to the skill store?** If yes,
   skill provenance stamping ships *first* (see the pedigree item). If scout
   output is reading material for a human or a run and never a store write,
   legs 3 and 4 shrink to nothing and the untrusted-git boundary never opens.
2. **Does the scout replace Phase-32 synthesis on a gap, or precede it?** Two
   writers answering one trigger needs an order — especially now that the
   trigger would fire for the first time.
3. **Trigger shape:** on-gap (reactive, pays at the worst moment) vs. scheduled
   sweep over the aggregated weak-match list. The telemetry above decides this.
4. **Sourcing:** the link-farm-first decree (2026-08-02) applies; method
   precedent is `docs/audit-2026-07/persona-skill-survey.md` — link farm
   first, targeted web checks after, demand ranked by recurrence.

---

## What I did not do

No code changed, nothing dispatched, no LLM calls. The box was read-only:
manifest census, `outcomes.jsonl` key scan, and a live import of
`skills.find_matching_skills` / `_tfidf_skill_rank` against the existing store.
No gap rate is quoted from this Mac — its workspace holds 5 run dirs, 1 with a
manifest, and the two-workspace ambiguity is still on the record.
