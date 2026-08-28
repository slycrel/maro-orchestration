import base64, io, json, os, sys, types

SRC = sys.argv[1]
sys.path.insert(0, SRC)

import execution_receipts as er


def b64d(s):
    return base64.b64decode(s.encode("ascii"))


def make_tree(base, entries):
    """Build the call-record filesystem a scenario describes.

    Order is the spec's, not sorted: a scenario may layer a directory and
    a file that share a prefix, and the order it names them is the order
    it means.
    """
    os.makedirs(base, exist_ok=True)
    for e in entries:
        p = os.path.join(base, e["path"])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if e["kind"] == "dir":
            os.makedirs(p, exist_ok=True)
        elif e["kind"] == "sparse":
            # A file over MAX_FILE_BYTES without writing eight megabytes:
            # the size screen reads st_size, and a hole has one.
            with io.open(p, "wb") as fh:
                fh.truncate(e["size"])
        else:
            with io.open(p, "wb") as fh:
                fh.write(b64d(e["data"]))


def pv(v):
    """A canonical rendering both engines can produce.

    A dict becomes a LIST OF PAIRS because the key order is part of the
    answer — load_receipts writes its six keys in a fixed order and a
    consumer reading them back is entitled to it.
    """
    if isinstance(v, dict):
        return [[k, pv(x)] for k, x in v.items()]
    if isinstance(v, (list, tuple)):
        return [pv(x) for x in v]
    if isinstance(v, bool) or v is None or isinstance(v, (str, int, float)):
        return v
    return "<%s>" % type(v).__name__


def loaded_of(sc):
    """The `loaded` mapping a render scenario hands in.

    It arrives as JSON so a scenario can carry the WRONG types — a string
    count, a row that is not a mapping, a rows value that is not a list —
    which is most of what this function's error paths are about.
    """
    return json.loads(sc["loaded"])


def run_one(sc, root):
    kind = sc["kind"]
    out = {"name": sc["name"], "cls": "", "msg": ""}
    base = os.path.join(root, sc["name"])
    try:
        if kind == "clip":
            out["text"] = er._clip(sc["text"], sc["limit"])
        elif kind == "neutralize":
            out["text"] = er.neutralize_fence_text(sc["text"])
        elif kind == "display":
            out["text"] = er._display(json.loads(sc["value"]))
        elif kind == "path_token":
            out["tokens"] = er._PATH_TOKEN.findall(sc["text"])
        elif kind == "process_markers":
            out["hit"] = bool(er._PROCESS_MARKERS.search(sc["text"]))
        elif kind == "check_tokens":
            out["tokens"] = er._check_path_tokens(
                json.loads(sc["check_results"]))
        elif kind == "load":
            make_tree(base, sc["tree"])
            arg = base if sc["run_dir_is_path"] else json.loads(
                sc["run_dir_value"])
            out["loaded"] = pv(er.load_receipts(arg, json.loads(sc["cap"])))
        elif kind == "render":
            out["text"] = er.render_receipt_evidence(
                loaded_of(sc), json.loads(sc["check_results"]))
        elif kind == "audit":
            make_tree(base, sc["tree"])
            mod = types.ModuleType("runs")

            def current_run_dir(_base=base, _sc=sc):
                if _sc["run_dir_raises"]:
                    raise RuntimeError("no run")
                return None if _sc["run_dir_none"] else _base
            mod.current_run_dir = current_run_dir
            if sc["runs_import_fails"]:
                sys.modules.pop("runs", None)
                blocker = _pyprobe_block(["runs"])
            else:
                sys.modules["runs"] = mod
                blocker = None
            logged = []
            er.log.debug = lambda fmt, *a: logged.append(fmt % a)
            try:
                out["text"] = er.audit_receipt_block(
                    json.loads(sc["check_results"]))
            finally:
                if blocker is not None:
                    _pyprobe_unblock(blocker)
                sys.modules.pop("runs", None)
            out["logged"] = logged
        else:
            raise AssertionError("unknown kind %s" % kind)
    except BaseException as e:
        out["cls"] = type(e).__name__
        out["msg"] = str(e)
    return out


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
root = sys.argv[3]
print(json.dumps([run_one(sc, root) for sc in spec]))
