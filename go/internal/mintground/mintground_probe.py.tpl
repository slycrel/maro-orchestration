import base64, io, json, os, sys, types

SRC = sys.argv[1]
sys.path.insert(0, SRC)

import mint_grounding as mg


def b64d(s):
    return base64.b64decode(s.encode("ascii"))


def make_tree(base, entries):
    """Build the run tree a scenario describes.

    Order is the spec's, not sorted: a symlink may point at something a
    later entry creates, and the order a scenario names them is the order
    it means. File bodies travel as base64 so a scenario can seed the raw
    ill-formed bytes that errors="replace" is about.
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


def install_runs(sc, base):
    """The `from runs import resolve_run_dir` seam.

    Both halves of the Python failure are reachable: the import not
    resolving at all, and the call raising. The real runs module is on
    sys.path, so a scenario that wants neither MUST say so -- otherwise
    the probe would import the production resolver and read this box's
    live workspace.
    """
    if sc["import_fails"]:
        sys.modules.pop("runs", None)
        return _pyprobe_block(["runs"])
    mod = types.ModuleType("runs")

    def resolve_run_dir(ref, _sc=sc, _base=base):
        if _sc["resolve_raises"]:
            raise RuntimeError("no such run")
        if not _sc["resolve_to"]:
            return None
        from pathlib import Path
        return Path(os.path.join(_base, _sc["resolve_to"]))
    mod.resolve_run_dir = resolve_run_dir
    sys.modules["runs"] = mod
    return None


def event_json(e):
    return {"ref": e["ref"], "name": e["name"], "input": e["input"],
            "output": e["output"], "is_error": e["is_error"]}


def stamps_json(stamps):
    """Stamps as they are, INCLUDING which keys are absent.

    The tied-supported stamp carries no "note" key at all, and that
    absence is load-bearing upstream, so it is reported rather than
    normalised away.
    """
    return [dict(s) for s in stamps]


def run_one(sc, root_dir):
    kind = sc["kind"]
    out = {"name": sc["name"], "cls": "", "msg": ""}
    base = os.path.join(root_dir, sc["name"])
    blocker = None
    saved_cap = None
    try:
        if kind == "sentences":
            import re
            out["parts"] = re.split(r"(?<=[.;!?])\s+|\n+", sc["text"])
        elif kind == "instruction":
            out["hit"] = mg._is_instruction(sc["text"])
        elif kind == "retrospective":
            out["hit"] = mg._is_retrospective(sc["text"])
        elif kind == "clause_tail":
            out["tail"] = mg._clause_tail(sc["text"])
        elif kind == "claims":
            out["claims"] = [dict(c) for c in mg.extract_claims(sc["text"])]
        elif kind == "tie_tokens":
            out["toks"] = mg._tie_tokens(sc["text"])
        elif kind == "specific_tokens":
            out["toks"] = mg._specific_tokens(mg._tie_tokens(sc["text"]))
        elif kind == "event_preds":
            ev = json.loads(sc["event"])
            out["preds"] = {
                "exec": mg._is_exec(ev), "fetch": mg._is_fetch(ev),
                "auth": mg._is_auth(ev), "test": mg._is_test(ev),
                "probe": mg._is_probe(ev)}
        elif kind == "ground_text":
            out["stamps"] = stamps_json(
                mg.ground_text(sc["text"], json.loads(sc["events"])))
        elif kind == "summary":
            out["text"] = mg.grounding_summary(json.loads(sc["grounding"]))
        elif kind == "marker":
            out["text"] = mg.grounding_marker(json.loads(sc["grounding"]))
        elif kind == "has_unsupported":
            out["hit"] = mg.has_unsupported(json.loads(sc["grounding"]))
        elif kind in ("collect", "ground_lessons"):
            make_tree(base, sc["tree"])
            if sc["cap_override_set"]:
                saved_cap = mg._MAX_EVENTS
                mg._MAX_EVENTS = sc["cap_override"]
            if kind == "collect":
                evs = mg.collect_run_tool_events(
                    os.path.join(base, sc["run_dir"]) if sc["run_dir"]
                    else base)
                out["present"] = evs is not None
                out["events"] = ([event_json(e) for e in evs]
                                 if evs is not None else [])
            else:
                blocker = install_runs(sc, base)
                out["stamps"] = [stamps_json(g) for g in
                                 mg.ground_lessons_for_run(sc["lessons"],
                                                           sc["run_ref"])]
        else:
            raise AssertionError("unknown kind %s" % kind)
    except BaseException as e:
        out["cls"] = type(e).__name__
        out["msg"] = str(e)
    finally:
        if saved_cap is not None:
            mg._MAX_EVENTS = saved_cap
        if blocker is not None:
            _pyprobe_unblock(blocker)
        sys.modules.pop("runs", None)
    return out


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
root_dir = sys.argv[3]
print(json.dumps([run_one(sc, root_dir) for sc in spec]))
