"""Export-side root placeholders: the round trip must be byte-exact.

Archives are byte-faithful today and that has already paid off (the flat
lesson ledger was restored 466/466 rows straight out of one). Tokenizing on
export keeps that property only while expansion restores the original bytes,
so the load-bearing tests here are round trips through a real archive.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXPORT = REPO / "scripts" / "maro_export.py"


def _run(args, env_ws: Path, cwd=None):
    import os
    env = dict(os.environ)
    env["MARO_WORKSPACE"] = str(env_ws)
    env["PYTHONPATH"] = str(REPO / "src")
    return subprocess.run([sys.executable, str(EXPORT), *args],
                          capture_output=True, text=True, env=env, cwd=cwd)


@pytest.fixture()
def ws(tmp_path):
    w = tmp_path / "workspace"
    (w / "runs" / "abc-run" / "build").mkdir(parents=True)
    (w / "memory").mkdir(parents=True)
    return w


def _archive_member(arc: Path, suffix: str) -> bytes:
    with tarfile.open(arc, "r:gz") as tf:
        for m in tf.getmembers():
            if m.isreg() and m.name.endswith(suffix):
                return tf.extractfile(m).read()
    raise AssertionError(f"member ending {suffix} not found")


def test_owned_path_is_tokenized_in_the_archive(ws, tmp_path):
    """A reference to our own data becomes portable."""
    rec = ws / "runs" / "abc-run" / "build" / "calls.json"
    rec.write_text(json.dumps({"call_record": f"{ws}/runs/abc-run/build/c.json"}))
    out = tmp_path / "a.tar.gz"
    r = _run(["export", "--output", str(out)], ws)
    assert r.returncode == 0, r.stderr
    body = _archive_member(out, "build/calls.json").decode()
    assert "%%MARO_WORKSPACE%%/runs/abc-run/build/c.json" in body
    assert str(ws) not in body


def test_observed_out_of_fence_path_survives_verbatim(ws, tmp_path):
    """The owned/observed line. A scavenge hit or write-fence violation names a
    path OUTSIDE our roots -- that absolute string IS the finding, and
    substituting it would destroy the evidence. It carries no root prefix, so
    it is left alone by construction rather than by a field list."""
    finding = "/home/clawd/fence-probe-stray.txt"
    rec = ws / "memory" / "captains_log.jsonl"
    rec.write_text(json.dumps({
        "event_type": "SCAVENGE_DETECTED",
        "context": {"writes": [finding]},
        "own": f"{ws}/runs/abc-run",
    }) + "\n")
    out = tmp_path / "a.tar.gz"
    assert _run(["export", "--output", str(out)], ws).returncode == 0
    body = _archive_member(out, "captains_log.jsonl").decode()
    assert finding in body, "an observed out-of-root path was rewritten"
    assert "%%MARO_WORKSPACE%%/runs/abc-run" in body, "our own ref stayed absolute"


def test_provenance_records_the_root_table_and_counts(ws, tmp_path):
    (ws / "memory" / "x.jsonl").write_text(f"{ws}/a\n{ws}/b\n")
    out = tmp_path / "a.tar.gz"
    assert _run(["export", "--output", str(out)], ws).returncode == 0
    prov = json.loads(_archive_member(out, "meta/provenance.json"))
    tok = prov["path_tokens"]
    assert tok["applied"] is True
    assert tok["occurrences"]["%%MARO_WORKSPACE%%"] >= 2
    roots = {r["token"]: r for r in tok["roots"] if r["canonical"]}
    assert roots["%%MARO_WORKSPACE%%"]["root"] == str(ws)


def test_binary_content_is_not_tokenized(ws, tmp_path):
    """Reuses path_rewrite's whole-file NUL screen -- a path spliced into a
    binary tail is the failure that screen was hardened for."""
    blob = ws / "memory" / "blob.dat"
    blob.write_bytes(b"\x00\x01" + str(ws).encode() + b"\x00")
    out = tmp_path / "a.tar.gz"
    assert _run(["export", "--output", str(out)], ws).returncode == 0
    assert str(ws).encode() in _archive_member(out, "blob.dat")


def test_export_fails_closed_when_a_token_already_occurs(ws, tmp_path):
    """A pre-existing token makes the transform non-invertible. Shipping that
    archive is worse than shipping none."""
    (ws / "memory" / "x.jsonl").write_text(
        "%%MARO_WORKSPACE%% was already here\n" + f"{ws}/a\n")
    r = _run(["export", "--output", str(tmp_path / "a.tar.gz")], ws)
    assert r.returncode != 0
    assert "not be invertible" in (r.stderr + r.stdout)


def test_tokenize_can_be_disabled(ws, tmp_path):
    sys.path.insert(0, str(REPO / "src"))
    sys.path.insert(0, str(REPO / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("maro_export_mod", EXPORT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (ws / "memory" / "x.jsonl").write_text(f"{ws}/a\n")
    import os
    os.environ["MARO_WORKSPACE"] = str(ws)
    out = mod.export_workspace(tmp_path / "b.tar.gz", tokenize=False)
    body = _archive_member(Path(out), "x.jsonl").decode()
    assert str(ws) in body and "%%MARO_" not in body


# ------------------------------------------------------------- round trip

def test_export_then_import_restores_the_original_bytes(ws, tmp_path):
    """The whole contract. Substitution is prefix-only and the token cannot
    occur in source content (asserted at export), so expansion is an exact
    inverse -- an imported copy is byte-identical to what was exported."""
    rec = ws / "runs" / "abc-run" / "build" / "calls.json"
    original = json.dumps({
        "call_record": f"{ws}/runs/abc-run/build/c.json",
        "observed": "/home/clawd/fence-probe-stray.txt",
    })
    rec.write_text(original)
    arc = tmp_path / "a.tar.gz"
    assert _run(["export", "--output", str(arc)], ws).returncode == 0

    dest = tmp_path / "dest"
    dest.mkdir()
    r = _run(["import", str(arc)], dest)
    assert r.returncode == 0, r.stderr
    restored = (dest / "runs" / "abc-run" / "build" / "calls.json").read_text()

    # our own reference now points at THIS install's workspace...
    assert f"{dest}/runs/abc-run/build/c.json" in restored
    # ...the observed path is untouched...
    assert "/home/clawd/fence-probe-stray.txt" in restored
    # ...and no placeholder leaked into the live workspace.
    assert "%%MARO_" not in restored


def test_same_machine_round_trip_is_byte_identical(ws, tmp_path):
    """Exporting and re-importing to the same roots must reproduce the input
    exactly -- the invertibility guarantee, tested rather than argued."""
    rec = ws / "memory" / "x.jsonl"
    original = (f"{ws}/runs/a/x.json\n"
                f"/home/clawd/.openclaw/foreign.txt\n"
                f"{ws}/runs/b/y.json\n").encode()
    rec.write_bytes(original)
    arc = tmp_path / "a.tar.gz"
    assert _run(["export", "--output", str(arc)], ws).returncode == 0

    rec.unlink()
    assert _run(["import", str(arc)], ws).returncode == 0
    assert rec.read_bytes() == original, "round trip was not byte-identical"


# ------------------------------------------------------------- tripwire

def test_no_placeholder_ever_survives_into_a_live_workspace(ws, tmp_path):
    """Step 5 of the build order. A placeholder reaching a LIVE store means an
    expansion was skipped somewhere, and the failure is otherwise silent --
    the ledger restore read rows straight out of an archive and would have
    written token text into a live store."""
    (ws / "memory" / "x.jsonl").write_text(f"{ws}/runs/a\n")
    (ws / "runs" / "abc-run" / "build" / "c.json").write_text(
        json.dumps({"p": f"{ws}/runs/abc-run"}))
    arc = tmp_path / "a.tar.gz"
    assert _run(["export", "--output", str(arc)], ws).returncode == 0

    dest = tmp_path / "live"
    dest.mkdir()
    assert _run(["import", str(arc)], dest).returncode == 0

    offenders = []
    for p in dest.rglob("*"):
        if not p.is_file() or p.suffix.lower() in {".gz", ".db"}:
            continue
        try:
            b = p.read_bytes()
        except OSError:
            continue
        if b"%%MARO_" in b and ".import-meta" not in str(p):
            offenders.append(str(p.relative_to(dest)))
    assert not offenders, f"placeholder text left in a live workspace: {offenders}"


# ============================================================ review 2026-08-20

def test_import_does_not_touch_members_export_refused_to_tokenize(ws, tmp_path):
    """Export screens binaries out; import used to expand tokens in EVERY
    regular member, so a literal token occurring inside a binary was rewritten
    -- corrupting bytes export had promised to preserve. The two sides now
    screen by one recorded member list instead of two drifting rules."""
    (ws / "memory" / "text.jsonl").write_text(f"{ws}/runs/a\n")
    blob = ws / "memory" / "blob.dat"
    original = b"BIN\x00%%MARO_WORKSPACE%%/deep\x00tail"
    blob.write_bytes(original)
    arc = tmp_path / "a.tar.gz"
    assert _run(["export", "--output", str(arc)], ws).returncode == 0

    dest = tmp_path / "dest"; dest.mkdir()
    assert _run(["import", str(arc)], dest).returncode == 0
    assert (dest / "memory" / "blob.dat").read_bytes() == original


def test_tokenized_archive_advertises_a_new_format(ws, tmp_path):
    """An importer that predates tokens caps at v2. A tokenized archive must
    trip its format gate rather than be extracted with placeholders intact."""
    (ws / "memory" / "x.jsonl").write_text(f"{ws}/runs/a\n")
    arc = tmp_path / "a.tar.gz"
    assert _run(["export", "--output", str(arc)], ws).returncode == 0
    prov = json.loads(_archive_member(arc, "meta/provenance.json"))
    assert prov["format"] == 3 and prov["path_tokens"]["applied"] is True


def test_untokenized_archive_stays_on_the_old_format(ws, tmp_path):
    """A workspace with no substitutable path must not gratuitously demand a
    newer importer."""
    (ws / "memory" / "x.jsonl").write_text("nothing path-shaped here\n")
    arc = tmp_path / "a.tar.gz"
    assert _run(["export", "--output", str(arc)], ws).returncode == 0
    prov = json.loads(_archive_member(arc, "meta/provenance.json"))
    assert prov["format"] == 2 and not prov["path_tokens"]["applied"]


def test_collision_leaves_no_artifact_at_the_requested_path(ws, tmp_path):
    """'Fails closed' was true of the process and false of the filesystem:
    tarfile.open truncates its target up front, so a late collision left a
    valid-looking partial archive where the operator asked for a real one."""
    for i in range(30):
        (ws / "memory" / f"f{i}.jsonl").write_text(f"{ws}/runs/{i}\n")
    (ws / "memory" / "zzz.jsonl").write_text("%%MARO_WORKSPACE%% already here\n")
    out = tmp_path / "a.tar.gz"
    assert _run(["export", "--output", str(out)], ws).returncode != 0
    assert not out.exists(), "partial archive left at the requested path"
    assert not Path(str(out) + ".partial").exists(), "partial stub left behind"


def test_a_pre_existing_archive_survives_a_failed_export(ws, tmp_path):
    out = tmp_path / "a.tar.gz"
    out.write_bytes(b"PREVIOUS GOOD ARCHIVE")
    (ws / "memory" / "x.jsonl").write_text("%%MARO_WORKSPACE%% already here\n")
    assert _run(["export", "--output", str(out)], ws).returncode != 0
    assert out.read_bytes() == b"PREVIOUS GOOD ARCHIVE"


def test_provenance_always_carries_the_alias_accounting_key(ws, tmp_path):
    """Alias and symlink spellings normalise on expansion by design (an
    approved decision), so those occurrences are NOT byte-identical to the
    source. Import announces them from this key, so it must always be present
    -- an absent key and a zero count must not be the same thing to a reader.

    The count itself is exercised at unit level in test_path_tokens.py; this
    checkout's directory name happens to equal the alias, so a CLI-level alias
    hit is not reproducible here and is not faked.
    """
    (ws / "memory" / "x.jsonl").write_text(f"{ws}/runs/a\n")
    arc = tmp_path / "a.tar.gz"
    assert _run(["export", "--output", str(arc)], ws).returncode == 0
    tok = json.loads(_archive_member(arc, "meta/provenance.json"))["path_tokens"]
    assert "alias_normalized" in tok
    assert isinstance(tok["alias_normalized"], dict)
    assert tok["members_rewritten"] == len(tok["members"])


@pytest.mark.parametrize("forged,why", [
    ({"applied": "false"}, "string-typed boolean is truthy"),
    ({"applied": True, "members": [], "members_rewritten": 0,
      "occurrences": {}}, "applied with no roots"),
    ({"applied": True, "roots": [{"token": "%%MARO_WORKSPACE%%", "root": "/w"}],
      "members": ["a", "b"], "members_rewritten": 1, "occurrences": {}},
     "declared count disagrees with the member list"),
    ({"applied": True, "roots": [{"token": "%%MARO_FUTURE%%", "root": "/w"}],
      "members": [], "members_rewritten": 0, "occurrences": {}},
     "token from a newer exporter"),
])
def test_forged_token_metadata_is_refused(forged, why):
    """This metadata selects a destructive transform over an extracted
    workspace, so it is a trust boundary."""
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "src"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("maro_export_v", EXPORT)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod._validate_path_tokens(forged), why


def test_absent_token_metadata_is_accepted():
    """A v2 archive is legitimate, not forged -- the negative control."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("maro_export_v2", EXPORT)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod._validate_path_tokens({}) == []
