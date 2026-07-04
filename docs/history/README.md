# docs/history/

Point-in-time records. Every file here was **correct as of its date** and is kept
as evidence, provenance, and dev-recall context — none of it is current truth.
For current truth: `GOAL_BRAIN.md` → `MILESTONES.md` → `docs/INDEX.md`.

Naming:
- `YYYY-MM-DD-name.md` — a snapshot/record; the date is the last *substantive*
  edit (verified against git history, mechanical rename-sweep commits excluded).
- Undated files (`CHANGELOG.md`, `ROADMAP_ARCHIVE.md`) — **rolling logs** that
  accreted over a span rather than describing one moment. CHANGELOG was
  abandoned 2026-06-21 (git log + GOAL_BRAIN supersede it); ROADMAP_ARCHIVE
  holds every completed phase (0–62) and is still ingested by dev-recall.

Why `BACKLOG_DONE.md` is NOT here: it's part of the active end-of-chunk
workflow at repo root (BACKLOG.md items move there with context when shipped)
and is a dev-recall explicit-path source. It's an archive that's still written
to weekly — a living file with historical content.

Everything in this directory is ingested by dev-recall automatically (it walks
`docs/` recursively), so moving a file here never removes it from retrieval.
