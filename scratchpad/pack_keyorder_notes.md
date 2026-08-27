# `pack` key-order fix — the exact insertion orders, read off `src/pack.py`

Research done 2026-08-26 while r11 was running (P4: no Go edits during a
battery). Everything below is READ from the Python, with the line number,
so the Go fix does not have to re-derive it.

The Go builds these with `map[string]any`, whose `encoding/json` output is
**alphabetical**. Python's `json.dumps` emits **insertion order**. Every
object below has to become a `pyval.Obj` (ordered pairs) in the Go.

## `pack.json` — `build_manifest`, pack.py:185-203

```
pack_format
name
created_at
origin        -> {label, maro_version, scrubber_version}
artifacts     -> a list; per-entry order below
review        -> {human_reviewed, reviewed_at,
                  review_manifest_sha256, review_payload_sha256}
trust_policy
```

**The subtlety that will be got wrong:** `seal_pack` (pack.py:457) does
`manifest["review"] = {...}`, and assigning to an EXISTING key does not move
it — `review` stays in position 6, between `artifacts` and `trust_policy`.
A Go port that rebuilds the manifest on seal, or appends the replacement,
puts it last. The sealed and unsealed manifests must differ only in the
four values inside `review`.

## Artifact entries — TWO shapes, not one

Text artifacts, `_add_text_artifact` at pack.py:319:

```
class, path, sha256
```

JSONL artifacts, pack.py:350-357 — note the CONDITIONAL key lands in the
middle, because `sha256` is assigned after it:

```
class, path, rows, [quarantined_rows_skipped,] sha256
```

This is exactly what the write-path harness reported: CPython emitted
`path, rows, quarantined_rows_skipped, sha256` and the Go emitted
`path, quarantined_rows_skipped, rows, sha256` (alphabetical). Absence of
the key is also meaningful — it is omitted when zero, not written as 0.

## `import_pack` report — pack.py:1103-1110

```
pack, pack_tag, label, imported_at, dry_run,
rules_demoted_to_hypotheses, hypotheses_imported,
lessons_imported, skill_records_imported,
skills_md, personas_md,
quarantined, quarantined_unknown
```

The audit row appended to `memory/imports.jsonl` is
`{**report, "action": "pack_import"}` (pack.py:1154) — `action` is LAST,
because it is a new key added to a copy.

Quarantine rows (pack.py:1144-1148) are `{"class": cls, **_quarantine_single(...)}`
and `_quarantine_single` returns `{path, outcome}` (pack.py:1016-1019), so:

```
class, path, outcome
```

## `adopt` report — pack.py:1240

```
label, adopted, skipped, adopted_at, dry_run
```

and its audit row is `{**report, "action": "adopt"}` — `action` last again.

## Order of work when the Go tree is free again

1. `pyval.Obj` for the manifest, the two artifact-entry shapes, and both
   reports. The two artifact shapes are the part most likely to be
   collapsed into one builder by accident — they are different objects,
   not one object with an optional field.
2. `origin.maro_version`: decide, do not paper over. `"go-port"` is honest
   about which engine wrote the pack and `"0.8.0"` is what a Python
   importer's compatibility check will expect. This is a real decision and
   it should be recorded as one.
3. The MODE class is separate work with its own census (BACKLOG).
4. `import --dry-run` creating `inbox/memory/long` and `inbox/memory/medium`
   on the Python side: find WHERE, and decide which engine is right about
   what `--dry-run` means before matching either.
5. Re-run `go/tools/write-compare.py` — it is the acceptance test, and it
   already reports these five as the whole differing set.

---

## Addendum: why the Python's `--dry-run` writes

Traced 2026-08-26. `_import_lessons` (pack.py:698-699) calls
`load_tiered_lessons` for MEDIUM and LONG to build the dedup sets, BEFORE
it decides anything and regardless of `dry_run`. That read goes through
`knowledge_web._tiered_lessons_path` (knowledge_web.py:300-303):

```python
def _tiered_lessons_path(tier: str) -> Path:
    d = _memory_dir() / tier
    d.mkdir(parents=True, exist_ok=True)     # <- on the READ path
    return d / "lessons.jsonl"
```

So the two directories a `--dry-run` import leaves behind are a **side
effect of a path helper**, not a deliberate dry-run write. That changes the
shape of the decision: this is not "the Go forgot to create two
directories", it is "the Python's dry run is not inert and probably did not
mean to be". Both "match it" and "stay inert" are defensible, and the
choice should be recorded as a decision either way rather than settled by
whichever is easier to implement.

Worth noting for the port's stated case: this is a latent behaviour in
production Python that only became visible because a second implementation
had to agree with it.
