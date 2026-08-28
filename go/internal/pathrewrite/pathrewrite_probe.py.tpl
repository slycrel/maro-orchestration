import base64, io, json, os, sys

SRC = sys.argv[1]
sys.path.insert(0, SRC)

import path_rewrite as pr


def b64d(s):
    return base64.b64decode(s.encode("ascii"))


def b64e(b):
    return base64.b64encode(b).decode("ascii")


def value_of(kind, raw):
    """Rebuild the argument a scenario names.

    validate_root's FIRST test is isinstance(value, str) and its message
    carries type(value).__name__, so the non-string cases are the ones
    that matter — a spec that could only carry strings could not reach
    that branch at all.
    """
    if kind == "str":
        return raw
    if kind == "none":
        return None
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "bool":
        return raw == "True"
    if kind == "list":
        return [raw]
    raise AssertionError("unknown value kind %s" % kind)


def pairs_to_map(pairs):
    """A RewriteMap built directly, bypassing build_map.

    substitute() is tested against maps that build_map would refuse (a
    one-component source, a destination containing a later source), and
    building the frozen dataclass by hand is the only way to reach them.
    """
    return pr.RewriteMap(pairs=tuple((p[0], p[1]) for p in pairs),
                         rejected=())


def obj_pairs(d):
    return [[k, v] for k, v in d.items()]


def make_tree(base, entries):
    """Build the fixture filesystem a scenario describes.

    Order matters and is the spec's, not sorted: a symlink entry can name
    a target created by an earlier entry.
    """
    os.makedirs(base, exist_ok=True)
    for e in entries:
        p = os.path.join(base, e["path"])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        kind = e["kind"]
        if kind == "dir":
            os.makedirs(p, exist_ok=True)
        elif kind == "fifo":
            os.mkfifo(p)
        elif kind == "symlink":
            os.symlink(e["target"], p)
        else:
            with io.open(p, "wb") as fh:
                fh.write(b64d(e["data"]))
            # An empty mode/mtime means "leave it alone" — the
            # builder fills every field, so absence is not a signal
            # here and emptiness has to be.
            if e["mode"]:
                os.chmod(p, int(e["mode"], 8))
            if e["mtime"]:
                os.utime(p, (e["mtime"], e["mtime"]))


def file_state(base, entries):
    """What the tree looks like AFTER the call.

    The bytes are the point, but the mode and mtime are the two things a
    port silently loses: an atomic swap through a fresh temp file gets
    the umask's mode and today's mtime unless it is asked not to.
    """
    out = []
    for e in entries:
        if e["kind"] == "dir":
            # Reported because a directory sitting on the temp path is how
            # the failed-swap branch is reached, and whether it SURVIVES
            # is the whole question there.
            out.append([e["path"],
                        "<dir>" if os.path.isdir(
                            os.path.join(base, e["path"])) else "<gone>",
                        "", 0])
            continue
        if e["kind"] != "file":
            continue
        p = os.path.join(base, e["path"])
        try:
            st = os.lstat(p)
        except OSError:
            out.append([e["path"], "<gone>", "", 0])
            continue
        try:
            with io.open(p, "rb") as fh:
                data = b64e(fh.read())
        except OSError:
            # A mode-000 fixture is deliberate — it is how rewrite_file is
            # driven to "unreadable" — so the reader must survive it.
            data = "<unreadable>"
        out.append([e["path"], data, oct(st.st_mode & 0o7777),
                    int(st.st_mtime)])
    # A leftover temp file is a failed-swap symptom and belongs in the
    # record even though no scenario names it as an entry.
    leftovers = []
    for dirpath, _dirs, names in os.walk(base):
        for n in sorted(names):
            if n.endswith(pr._TMP_SUFFIX):
                leftovers.append(os.path.relpath(
                    os.path.join(dirpath, n), base))
    return out, sorted(leftovers)


def run_one(sc, root):
    kind = sc["kind"]
    out = {"name": sc["name"], "cls": "", "msg": ""}
    base = os.path.join(root, sc["name"])
    try:
        if kind == "validate":
            v = pr.validate_root(value_of(sc["value_kind"], sc["value"]),
                                 strict=sc["strict"])
            out["value"] = v
        elif kind == "build":
            m = pr.build_map(
                {k: value_of(t, v) for k, t, v in sc["source_roots"] or []},
                {k: value_of(t, v) for k, t, v in sc["dest_roots"] or []},
                **({"roles": tuple(sc["roles"])} if sc["roles"] is not None
                   else {}))
            out["pairs"] = [[s, d] for s, d in m.pairs]
            out["rejected"] = [[r, v, why] for r, v, why in m.rejected]
            out["describe"] = [obj_pairs(d) for d in m.describe()]
            # __bool__ is a method the port must have spelled separately;
            # a falsy map is what makes rewrite_tree return early.
            out["truthy"] = bool(m)
        elif kind == "substitute":
            m = pairs_to_map(sc["pairs"] or [])
            data, n = m.substitute(b64d(sc["data"]))
            out["data"] = b64e(data)
            out["count"] = n
        elif kind == "substitute_text":
            m = pairs_to_map(sc["pairs"] or [])
            text, n = m.substitute_text(sc["text"])
            out["text"] = text
            out["count"] = n
        elif kind == "skip":
            make_tree(base, sc["tree"])
            out["value"] = pr.skip_reason(
                sc["rel"], pr.Path(os.path.join(base, sc["rel"])))
        elif kind == "rewrite_file":
            make_tree(base, sc["tree"])
            m = pairs_to_map(sc["pairs"] or [])
            status, n = pr.rewrite_file(
                pr.Path(os.path.join(base, sc["rel"])), m,
                max_bytes=sc["max_bytes"])
            out["value"] = status
            out["count"] = n
            out["files"], out["leftovers"] = file_state(base, sc["tree"])
        elif kind == "rewrite_tree":
            make_tree(base, sc["tree"])
            m = pr.build_map(
                {k: value_of(t, v) for k, t, v in sc["source_roots"] or []},
                {k: value_of(t, v) for k, t, v in sc["dest_roots"] or []})
            rep = pr.rewrite_tree(pr.Path(base), sc["rel_names"] or [], m,
                                  max_bytes=sc["max_bytes"])
            rec = rep.as_record()
            rec["mapping"] = [obj_pairs(x) for x in rec["mapping"]]
            rec["rejected_roots"] = [obj_pairs(x)
                                     for x in rec["rejected_roots"]]
            rec["skipped"] = obj_pairs(rec["skipped"])
            rec["files"] = [obj_pairs(x) for x in rec["files"]]
            out["record"] = obj_pairs(rec)
            out["summary"] = rep.summary()
            out["files_after"], out["leftovers"] = file_state(base, sc["tree"])
        else:
            raise AssertionError("unknown kind %s" % kind)
    except BaseException as e:
        out["cls"] = type(e).__name__
        out["msg"] = str(e)
    return out


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
root = sys.argv[3]
print(json.dumps([run_one(sc, root) for sc in spec]))
