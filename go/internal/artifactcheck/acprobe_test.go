package artifactcheck

// The CPython side of this package's differential.
//
// acPySrc is src/artifact_check.py's ground-truth probe, carried here from
// the pre-code capture that was written and RUN before any Go in this
// package existed (L49). Two things about it are worth stating because
// both are the kind of thing a later reader would otherwise have to guess:
//
//   - It is verbatim apart from ONE additive case kind, `field_order`,
//     which asks `dataclasses.fields(ArtifactVerdict)` for the field order
//     itself. The original probe compared against a `vdict()` helper the
//     probe author hand-wrote, which pins ToDict against that author's
//     opinion rather than against the dataclass. The dataclass is the
//     source; the helper is a transcription of it.
//
//   - It contains no backtick, which is the only reason it can live in a
//     Go raw string literal at all. A backtick inside the Python would
//     terminate the literal three tokens into the file, and the resulting
//     error names a line hundreds of lines away from the cause. Every
//     fixture that needs one keeps it on the GO side, where an interpreted
//     string can spell it.
//
// The probe takes a JSON array of cases on argv[1] and returns a JSON
// array of {"ok": <value>} or {"err": "<Type>: <message>"}, positionally
// aligned with its input. An "err" is always a failure here: this probe's
// whole job is to answer, and a case it cannot answer is a case the
// comparison silently skipped.
const acPySrc = `
import json, os, pathlib, shutil, sys, time
import artifact_check as ac


def mktree(root, files, mtimes=None):
    """Build a tree. files maps relpath -> text (None makes a directory)."""
    base = pathlib.Path(root)
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = base / rel
        if text is None:
            p.mkdir(parents=True, exist_ok=True)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    for rel, ts in (mtimes or {}).items():
        os.utime(base / rel, (ts, ts))
    return base


def vdict(v):
    return {"fabricated": v.fabricated, "claims": v.claims, "missing": v.missing,
            "changed_count": v.changed_count, "reason": v.reason,
            "kind": v.kind, "judged": v.judged}


ROOT = os.path.join(os.environ["MARO_WORKSPACE"], "acwork")
out = []
for c in json.loads(sys.argv[1]):
    try:
        k = c["kind"]
        if k == "clean_token":
            out.append({"ok": ac._clean_token(c["tok"])})
        elif k == "claims":
            out.append({"ok": ac.extract_write_claims(c["text"])})
        elif k == "stdout_claim":
            out.append({"ok": ac._claims_concrete_stdout(c["text"])})
        elif k == "clean_success":
            out.append({"ok": ac._claims_clean_success(c["text"])})
        elif k == "tool_failed":
            out.append({"ok": ac._tool_failed(c["te"])})
        elif k == "exec_claim":
            out.append({"ok": vdict(ac.check_execution_claim(
                c["text"], c["tool_events"]))})
        elif k == "snapshot":
            base = mktree(ROOT, c["files"], c.get("mtimes"))
            snap = ac.snapshot_dir(str(base) if c.get("real", True) else c.get("root"))
            out.append({"ok": sorted(snap.keys())})
        elif k == "snapshot_missing":
            out.append({"ok": sorted(ac.snapshot_dir(c["root"]).keys())})
        elif k == "changed":
            base = mktree(ROOT, c["files"], c.get("mtimes"))
            before = {k2: float(v2) for k2, v2 in c["before"].items()}
            out.append({"ok": sorted(ac.changed_since(before, str(base)))})
        elif k == "modified_since":
            base = mktree(ROOT, c["files"], c.get("mtimes"))
            out.append({"ok": ac.files_modified_since(
                str(base), c["since"], limit=c.get("limit", 20))})
        elif k == "exists":
            base = mktree(ROOT, c["files"])
            arg = c["claim"]
            if c.get("abs"):
                arg = str(base / c["claim"])
            out.append({"ok": ac._exists_anywhere(
                arg, str(base) if c.get("with_dir", True) else None)})
        elif k == "fabrication":
            base = mktree(ROOT, c["files"], c.get("mtimes"))
            before = {k2: float(v2) for k2, v2 in c.get("before", {}).items()}
            out.append({"ok": vdict(ac.check_fabrication(
                c["text"], str(base) if c.get("with_dir", True) else None, before))})
        elif k == "snapshot_links":
            # os.walk defaults to followlinks=False, so a symlinked DIRECTORY
            # is listed as an entry of its parent and never descended into;
            # a symlinked FILE is stat()ed through the link. Both are real
            # shapes in a workspace and neither is stated anywhere in the
            # Python -- they are inherited from os.walk's defaults, which is
            # exactly the kind of thing a port re-decides by accident.
            base = mktree(ROOT, c["files"])
            outside = pathlib.Path(str(base) + "-target")
            if outside.exists():
                shutil.rmtree(outside)
            outside.mkdir(parents=True, exist_ok=True)
            (outside / "hidden.txt").write_text("h", encoding="utf-8")
            os.symlink(str(outside), str(base / "linkdir"))
            os.symlink(str(outside / "hidden.txt"), str(base / "linkfile.txt"))
            out.append({"ok": sorted(ac.snapshot_dir(str(base)).keys())})
        elif k == "candidates":
            base = mktree(ROOT, c["files"])
            cands = ac._python_candidates(
                c["claims"], str(base) if c.get("with_dir", True) else None,
                set(c.get("changed", [])))
            out.append({"ok": [os.path.relpath(str(p2), str(base))
                               for p2 in cands]})
        elif k in ("inert_verdict", "fabrication2"):
            # Layer 2 minus the ONE thing Go cannot express. _python_is_inert
            # is CPython ast; everything around it -- the two-part claim gate,
            # candidate collection, and the three fail-open returns -- is
            # ordinary logic, and it is the part the port has to get right.
            # Patch the predicate with a scripted answer per BASENAME so the
            # surrounding flow is measurable on its own.
            base = mktree(ROOT, c["files"], c.get("mtimes"))
            script = c["inert"]           # basename -> true / false / null
            real_pred = ac._python_is_inert

            def _fake(source, _sc=script, _b=base):
                for name, verdict in _sc.items():
                    try:
                        if (_b / name).read_text(encoding="utf-8") == source:
                            return verdict
                    except OSError:
                        continue
                return None
            ac._python_is_inert = _fake
            try:
                if k == "inert_verdict":
                    v = ac._inert_output_verdict(
                        c["text"], str(base) if c.get("with_dir", True) else None,
                        c["claims"], set(c.get("changed", [])))
                    out.append({"ok": None if v is None else vdict(v)})
                else:
                    before = {k2: float(v2) for k2, v2 in c.get("before", {}).items()}
                    out.append({"ok": vdict(ac.check_fabrication(
                        c["text"],
                        str(base) if c.get("with_dir", True) else None, before))})
            finally:
                ac._python_is_inert = real_pred
        elif k == "consts":
            out.append({"ok": {"MAX_FILES": ac._MAX_FILES,
                               "MTIME_EPS": ac._MTIME_EPS,
                               "SKIP_DIRS": sorted(ac._SKIP_DIRS),
                               "EXECUTION_TOOLS": sorted(ac._EXECUTION_TOOLS)}})
        elif k == "field_order":
            import dataclasses
            out.append({"ok": [f.name for f in
                               dataclasses.fields(ac.ArtifactVerdict)]})
        elif k == "verdict_default":
            out.append({"ok": vdict(ac.ArtifactVerdict(False))})
        else:
            out.append({"err": "unknown kind " + k})
    except Exception as e:
        out.append({"err": type(e).__name__ + ": " + str(e)})
print(json.dumps(out))
`
