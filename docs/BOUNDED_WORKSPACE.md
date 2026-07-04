# Bounded Workspace — the containment spectrum

Where worker file access is allowed to land, what each tier protects against,
and what's actually implemented. Written for BACKLOG #1 (artifacts leaking into
the repo root / stale-clone scavenging).

**The fence** = the run's project dir (`~/.maro/workspace/projects/<slug>/`)
plus the canonical workspace (`~/.maro/workspace/`), plus (2026-07-04, after
the flip surfaced the trade-off):

- **/tmp, always** (`artifact_check.fence_allow_roots`; extend via config
  `validate.write_fence_allow`). Scratch is a normal worker move, not drift —
  Jeremy: "lean into /tmp... this isn't a sandbox." An in-fence scratch dir
  also exists at `~/.maro/workspace/tmp/` (created at loop entry) for scratch
  that should survive and stay inspectable.
- **Goal-declared paths** (`artifact_check.goal_declared_roots`): absolute or
  `~`-paths the goal text explicitly names join the fence for that run,
  audited via a `FENCE_EXTENDED` captain's-log row. Intent trumps — the fence
  enforces "the worker stayed where the goal pointed it", not "the goal is
  only allowed to want the workspace". Trust boundary: goals are trusted
  (Jeremy / dispatch navigator authored), workers aren't. Guardrails: system
  prefixes never widen (a goal naming `/etc/passwd` does not authorize it),
  bare top-level dirs (`/data`) are too broad to count, cap 8 roots.

NOW-lane interactive asks are exempt by design — an interactive ask
legitimately runs where it was launched.

## The three tiers

| Tier | What | Protects against | Cost | Status |
|---|---|---|---|---|
| **(a) hard fence** | Enforcement: out-of-fence access is detected from the real tool transcript and *changes the run* (step demoted, escalation) | Contamination in both directions — strays landing in version control / other projects, AND scavenging stale sources into results | Detection heuristics can false-positive; blocked-step recovery cost on trips | Detection SHIPPED (always-on); write-fence demotion SHIPPED, config-gated **off** (`validate.write_fence`) pending watch-row evidence. Container/docker isolation (the full version of this tier) is deliberately out of scope — "constraint to a folder isn't a bad option to have", not a sandboxing subsystem |
| **(b) soft fence** | Convention + defaults: every agentic subprocess is spawned with its cwd bound inside the fence, so relative writes land in-workspace by construction | The observed default failure mode (cwd-relative strays); nothing stops a deliberate absolute-path write or a mid-command `cd` | Near zero | SHIPPED 2026-06-26 → 2026-07-03 (executor cwd bind, run-scoped ambient cwd ContextVar, unconditional loop-entry bind incl. project-less dispatches) |
| **(c) full machine** | No constraint — worker reads/writes anywhere | Nothing; maximum capability | Free | The pre-2026-06-26 default, and still the NOW-lane behavior (by design) |

## When to use which

- **Default (AGENDA loop): (b) + (a)-detection.** The cwd fence makes honest
  workers land in the right place; the scavenge detector makes dishonest or
  drifting ones visible (`SCAVENGE_DETECTED` captain's-log rows, reads and
  writes flagged separately).
- **(a)-enforcement (`validate.write_fence: true`):** ENABLED on the box
  2026-07-04 (Jeremy's flip) and live-proven both directions same day. An
  out-of-fence write demotes the step done→blocked with the evidence in
  `FENCE_WRITE_BLOCKED`; the blocked-step machinery then retries with a hint
  (tier-up) unless the navigator is ≥0.9 confident retry is futile. The two
  legitimate out-of-fence write classes are handled structurally, not by
  keeping the fence off: /tmp is always allowed, and goal-declared target
  paths widen the fence per-run (see above).
- **(c):** interactive NOW-lane asks only.

## Detection mechanics (tier-a evidence layer)

`artifact_check.detect_out_of_fence_access(tool_events, fence_roots)` scans
each step's real tool transcript:

- **Structured tools** (Read/Glob/Grep/Write/Edit/...): absolute path inputs
  outside the fence are flagged (reads vs writes bucketed by tool).
- **Bash**: best-effort absolute-path regex over the command string, system
  prefixes (`/usr`, `/etc`, ...) filtered.
- **cwd drift** (evasion specimen run 668e46d1, 2026-07-04): a worker that
  `cd`'s out of the fence and writes with *relative* paths is caught by
  tracking `cd` targets across the step's Bash commands (worker cwd persists
  between Bash calls) and resolving relative write targets (`>`, `>>`, `tee`,
  relative structured writes) against the drifted cwd. Unresolvable cds
  (`cd $VAR`, `cd -`) silence the tracker rather than guessing — flags are
  positive evidence only.

Caps: 20 deduped paths per step (`truncated` flag set — no silent caps).
Config: `validate.scavenge_detect` (default on).

## Known holes (accepted for now)

- Bash writes via commands the regex doesn't model (`cp`/`mv`/`install`
  destinations, `python -c` writing files, `sed -i`) are invisible unless the
  path is absolute (then the main scan sees it, as a read).
- A worker could `cd` via constructs the tracker can't follow (subshells,
  `pushd`, `$VAR` targets) — the tracker goes silent instead of guessing, so
  those writes are missed, not mis-flagged.
- True isolation (tier (a) full: containers, mount namespaces) is out of
  scope by decree: "the goal is 'constraint to a folder isn't a bad option to
  have', not 'build a sandboxing subsystem'."
