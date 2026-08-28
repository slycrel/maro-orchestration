import base64, io, json, os, sys, types

SRC = sys.argv[1]
sys.path.insert(0, SRC)

import claim_verifier as cv


def b64d(s):
    return base64.b64decode(s.encode("ascii"))


def make_tree(base, entries):
    """Build the project tree a scenario describes.

    Order is the spec's, not sorted: a symlink may point at something a
    later entry creates, and the order a scenario names them is the order
    it means.
    """
    os.makedirs(base, exist_ok=True)
    for e in entries:
        p = os.path.join(base, e["path"])
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if e["kind"] == "dir":
            os.makedirs(p, exist_ok=True)
        elif e["kind"] == "symlink":
            os.symlink(e["data"], p)
        else:
            with io.open(p, "wb") as fh:
                fh.write(b64d(e["data"]))


def install_llm(sc, base):
    """The `from llm import get_default_subprocess_cwd` seam.

    Both halves of the Python failure are reachable: the import not
    resolving at all, and the call raising. The real llm module is on
    sys.path, so a scenario that wants neither MUST say so — otherwise
    the probe would import the production adapter suite.
    """
    if sc["llm_import_fails"]:
        sys.modules.pop("llm", None)
        return _pyprobe_block(["llm"])
    mod = types.ModuleType("llm")

    def get_default_subprocess_cwd(_sc=sc, _base=base):
        if _sc["run_cwd_raises"]:
            raise RuntimeError("no run")
        if not _sc["run_cwd"]:
            return None
        return os.path.join(_base, _sc["run_cwd"])
    mod.get_default_subprocess_cwd = get_default_subprocess_cwd
    sys.modules["llm"] = mod
    return None


def root_arg(sc, base):
    from pathlib import Path
    if sc["root_is_none"]:
        return None
    return Path(os.path.join(base, sc["root"])) if sc["root"] else Path(base)


def rel(p, base):
    """Every path a record carries is reported RELATIVE to the scenario
    base: the absolute one names a temp directory that differs between the
    two engines by construction."""
    try:
        return os.path.relpath(str(p), base)
    except ValueError:
        return "<unrelated:%s>" % p


def claim_report_json(r):
    return {
        "raw_claims": list(r.raw_claims),
        "verified": list(r.verified),
        "not_found": list(r.not_found),
        "unresolvable": list(r.unresolvable),
        "suffix_matched": [[k, v] for k, v in r.suffix_matched.items()],
        "has_hallucinations": r.has_hallucinations,
        "rate": r.hallucination_rate,
        "summary": r.summary(),
    }


def symbol_report_json(r):
    return {
        "raw_claims": list(r.raw_claims),
        "verified": list(r.verified),
        "not_found": list(r.not_found),
        "has_hallucinations": r.has_hallucinations,
        "summary": r.summary(),
    }


def run_one(sc, root_dir):
    kind = sc["kind"]
    out = {"name": sc["name"], "cls": "", "msg": ""}
    base = os.path.join(root_dir, sc["name"])
    prev_cwd = os.getcwd()
    blocker = None
    saved_cap = None
    try:
        if kind == "file_claims":
            out["claims"] = cv.extract_file_claims(sc["text"])
        elif kind == "file_path_re":
            out["claims"] = [m.group(0)
                             for m in cv._FILE_PATH_RE.finditer(sc["text"])]
        elif kind == "symbol_claims":
            out["symbols"] = cv.extract_symbol_claims(sc["text"])
        elif kind == "synthesis":
            out["hit"] = cv.is_synthesis_step(sc["text"])
        elif kind == "claim_summary":
            d = json.loads(sc["report"])
            out["report"] = claim_report_json(cv.ClaimReport(
                raw_claims=d["raw_claims"], verified=d["verified"],
                not_found=d["not_found"], unresolvable=d["unresolvable"],
                suffix_matched={k: v for k, v in d["suffix_matched"]}))
        elif kind == "symbol_summary":
            d = json.loads(sc["report"])
            out["report"] = symbol_report_json(cv.SymbolReport(
                raw_claims=d["raw_claims"], verified=d["verified"],
                not_found=d["not_found"]))
        else:
            make_tree(base, sc["tree"])
            blocker = install_llm(sc, base)
            if sc["cap_override_set"]:
                saved_cap = cv._INDEX_MAX_DIRS
                cv._INDEX_MAX_DIRS = sc["cap_override"]
            os.chdir(os.path.join(base, sc["cwd"]) if sc["cwd"] else base)
            if kind == "infer_root":
                out["root"] = rel(cv._infer_project_root(), base)
            elif kind == "tree_index":
                names, relpaths = cv._tree_index(root_arg(sc, base))
                out["names"] = sorted(names)
                out["relpaths"] = sorted(relpaths)
            elif kind == "symbol_index":
                out["symbols"] = sorted(
                    cv._build_symbol_index(root_arg(sc, base)))
            elif kind == "verify_files":
                out["report"] = claim_report_json(cv.verify_file_claims(
                    sc["text"], project_root=root_arg(sc, base)))
            elif kind == "verify_symbols":
                out["report"] = symbol_report_json(cv.verify_symbol_claims(
                    sc["text"], project_root=root_arg(sc, base)))
            elif kind == "annotate":
                out["text"] = cv.annotate_result(
                    sc["text"], project_root=root_arg(sc, base),
                    only_if_hallucinations=sc["only_if_hallucinations"],
                    check_symbols=sc["check_symbols"])
            else:
                raise AssertionError("unknown kind %s" % kind)
    except BaseException as e:
        out["cls"] = type(e).__name__
        out["msg"] = str(e)
    finally:
        if saved_cap is not None:
            cv._INDEX_MAX_DIRS = saved_cap
        os.chdir(prev_cwd)
        if blocker is not None:
            _pyprobe_unblock(blocker)
        sys.modules.pop("llm", None)
    return out


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
root_dir = sys.argv[3]
print(json.dumps([run_one(sc, root_dir) for sc in spec]))
