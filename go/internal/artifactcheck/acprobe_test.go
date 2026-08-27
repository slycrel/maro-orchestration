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


def mktree(root, files, mtimes=None, parts=None):
    """Build a tree. files maps relpath -> text (None makes a directory).

    mtimes takes float seconds, the way most fixtures want them. parts
    takes [sec, nsec] and goes through utime(ns=...) instead, because a
    float second is not a portable way to ask for an exact timestamp and
    two fixtures are about exact timestamps: one at the ulp where two
    plausible float formulas differ, one past 2262 where a nanosecond
    count no longer fits in a signed 64-bit integer at all.
    """
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
    for rel, sn in (parts or {}).items():
        ns = int(sn[0]) * 10 ** 9 + int(sn[1])
        os.utime(base / rel, ns=(ns, ns))
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
        elif k == "changed_parts":
            base = mktree(ROOT, c["files"], None, c.get("mtime_parts"))
            before = {k2: float(v2) for k2, v2 in c.get("before", {}).items()}
            out.append({"ok": sorted(ac.changed_since(before, str(base)))})
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
        elif k == "exists_via_symlinked_dir":
            # Path(base) / claim leaves ".." for the KERNEL, which applies
            # it after resolving base. filepath.Join would remove it
            # TEXTUALLY, and the two name different files whenever base is
            # a symlink -- which on macOS every /tmp and /var path is.
            base = mktree(ROOT, c["files"])
            outside = pathlib.Path(str(base) + "-real")
            if outside.exists():
                shutil.rmtree(outside)
            (outside / "proj").mkdir(parents=True, exist_ok=True)
            (outside / "x.py").write_text("x = 1", encoding="utf-8")
            link = base / "link"
            if link.is_symlink():
                link.unlink()
            os.symlink(str(outside / "proj"), str(link))
            out.append({"ok": ac._exists_anywhere(c["claim"], str(link))})
        elif k == "candidates_nonregular":
            # p.is_file() is S_ISREG. A socket named like a module is not a
            # candidate; "exists and is not a directory" says it is, and one
            # such file makes ReadFile fail and DROPS a real inert verdict.
            #
            # The socket is bound in a SHORT directory and symlinked into
            # place, because a unix socket path is capped near 108 bytes
            # and a pytest-style temp dir named after this fixture is
            # already longer than that. Path.is_file() follows the link and
            # still answers False, which is the property under test.
            import socket, tempfile
            base = mktree(ROOT, c["files"])
            short = tempfile.mkdtemp(prefix="s")
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                target = os.path.join(short, "s")
                srv.bind(target)
                os.symlink(target, str(base / c["sock"]))
                out.append({"ok": [os.path.relpath(str(p), str(base))
                                   for p in ac._python_candidates(
                                       c["claims"], str(base),
                                       set(c.get("changed", [])))]})
            finally:
                srv.close()
                shutil.rmtree(short, ignore_errors=True)
        elif k == "snapshot_via_symlinked_dir":
            # os.walk yields dirpath verbatim and pathlib's / keeps the
            # ".." for the kernel. filepath.Join removes it textually, so
            # every stat in the walk missed and the snapshot came back empty.
            base = mktree(ROOT, c["files"])
            outside = base.parent / (base.name + "-svsd")
            if outside.exists():
                shutil.rmtree(outside)
            (outside / "proj").mkdir(parents=True, exist_ok=True)
            (outside / "a.txt").write_text("a", encoding="utf-8")
            (outside / "proj" / "deep.txt").write_text("d", encoding="utf-8")
            link = base / "slink"
            if link.is_symlink():
                link.unlink()
            os.symlink(str(outside / "proj"), str(link))
            root = os.path.join(str(link), "..")
            out.append({"ok": sorted(ac.snapshot_dir(root).keys())})
        elif k == "snapshot_symlinked_subdir":
            # W21's sibling, and the one branch W21 cannot reach: a
            # SYMLINKED DIRECTORY among the entries of a '..' root. scandir's
            # is_dir() follows the link, so os.walk puts it in dirnames and
            # then does not descend (followlinks=False) -- it contributes no
            # row at all. The port stat'd that probe through filepath.Join,
            # which erases the '..', so the stat missed, the link was
            # misclassified as a file, and it got a row of its own.
            base = mktree(ROOT, c["files"])
            outside = base.parent / (base.name + "-ssd")
            if outside.exists():
                shutil.rmtree(outside)
            (outside / "proj").mkdir(parents=True, exist_ok=True)
            (outside / "elsewhere").mkdir(parents=True, exist_ok=True)
            (outside / "a.txt").write_text("a", encoding="utf-8")
            (outside / "proj" / "deep.txt").write_text("d", encoding="utf-8")
            (outside / "elsewhere" / "inner.txt").write_text("i", encoding="utf-8")
            os.symlink(str(outside / "elsewhere"), str(outside / "sl2"))
            link = base / "slink"
            if link.is_symlink():
                link.unlink()
            os.symlink(str(outside / "proj"), str(link))
            root = os.path.join(str(link), "..")
            out.append({"ok": sorted(ac.snapshot_dir(root).keys())})
        elif k == "sorted_fsnames":
            # sorted() over surrogateescape-decoded str, which is what
            # Python holds every filename as. Names ride as lists of BYTE
            # VALUES in both directions because of the GO end, not this one:
            # json.dumps("\udc80b") is "\udc80b" here and loads back exactly,
            # but Go's encoding/json turns that escape into U+FFFD without
            # complaining -- which is why no ordinary fixture could ever
            # carry this input.
            base = pathlib.Path(ROOT) / "fsnames"
            if base.exists():
                shutil.rmtree(base)
            base.mkdir(parents=True, exist_ok=True)
            for vals in c["names"]:
                with open(os.path.join(os.fsencode(str(base)), bytes(vals)), "wb") as fh:
                    fh.write(b"x")
            got = ac.files_modified_since(str(base), c["since"], limit=c["limit"])
            out.append({"ok": [list(os.fsencode(n)) for n in got]})
        elif k == "repr_fsname":
            # repr() of a surrogate-escaped filename. The ANSWER is pure
            # ASCII -- repr writes the escape as six characters -- so this
            # one row can ride the ordinary string channel even though its
            # INPUT cannot, which is why the names arrive as byte values.
            out.append({"ok": [repr([os.fsdecode(bytes(v))]) for v in c["names"]]})
        elif k == "decode_replace":
            # bytes -> str with errors="replace" substitutes ONE U+FFFD per
            # MAXIMAL SUBPART, not per byte. Byte values ride as ints
            # because JSON has no byte string.
            outs = []
            for vals in c["values"]:
                d = bytes(vals).decode("utf-8", "replace")
                outs.append([d, len(d)])
            out.append({"ok": outs})
        elif k == "dst_transitions":
            # The naive .timestamp() branch goes through CPython's _mktime,
            # which invents a reading for a wall time that does not exist.
            # The GAP is where it and Go's time.Date part; the repeat hour
            # is where they agree, and the port's residual had named the
            # wrong one. Transitions are DISCOVERED from the live zone
            # rather than hardcoded, so this fixture cannot quietly become
            # a test of a date that is no longer a transition.
            import time as _t
            from datetime import datetime as _dt
            vals = []
            for x in c["values"]:
                try:
                    vals.append(_dt.fromisoformat(x).timestamp())
                except Exception:
                    vals.append(None)
            # The zone NAME is the last element rather than a sibling key:
            # the harness compares the probe's "ok" value against the port's
            # return, so a second key would never be looked at. Two engines
            # silently running in different zones must fail HERE, not look
            # like a value bug.
            vals.append(_t.tzname[_t.localtime().tm_isdst > 0])
            out.append({"ok": vals})
        elif k == "candidates_via_symlinked_dir":
            # The SECOND loop in _python_candidates joins the project dir to
            # a CHANGED relative path. Walk-derived keys never carry "..",
            # so the only way the two joins can disagree here is for the
            # project DIR to carry one -- which is ordinary (macOS /tmp and
            # /var are symlinks, and any caller may pass "<dir>/link/..").
            base = mktree(ROOT, c["files"])
            outside = base.parent / (base.name + "-cvsd")
            if outside.exists():
                shutil.rmtree(outside)
            (outside / "proj").mkdir(parents=True, exist_ok=True)
            (outside / "a.py").write_text("x = 1", encoding="utf-8")
            link = base / "clink"
            if link.is_symlink():
                link.unlink()
            os.symlink(str(outside / "proj"), str(link))
            proj = os.path.join(str(link), "..")
            out.append({"ok": [os.path.relpath(str(p), proj)
                               for p in ac._python_candidates(
                                   c["claims"], proj,
                                   set(c.get("changed", [])))]})
        elif k == "parse_iso":
            from datetime import datetime
            vals = []
            for x in c["values"]:
                try:
                    vals.append(datetime.fromisoformat(x).timestamp())
                except Exception:
                    vals.append(None)
            out.append({"ok": vals})
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
