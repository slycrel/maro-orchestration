"""Must-detect fixtures for the destructive-rewrite scanner.

"Found 0" is untrusted until fixtures prove the instrument can find.
Written after adversarial r4 (2026-08-17), where three lenses
independently found the scanner blind to the exact split-helper shape the
skills.py fix had just introduced — a scanner that silently omits a shape
reports "clean" for it forever.
"""

from __future__ import annotations

import ast
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from scan_destructive_rewrites import scan_module


def _scan(src: str):
    return {name: verdict.strip()
            for verdict, _lineno, name in scan_module(ast.parse(src))}


class TestTheScannerFindsTheShapesItExistsFor:
    def test_the_single_function_shape(self):
        # drop + write-back in one function — the original shape
        src = '''
def rewrite(path):
    out = []
    for line in path.read_text().splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        out.append(json.dumps(d))
    path.write_text("\\n".join(out))
'''
        assert _scan(src) == {"rewrite": "RISK"}

    def test_the_nested_closure_shape(self):
        # the rmw-callback shape: inner scans, outer writes
        src = '''
def outer(path):
    def _merge(old):
        out = []
        for line in old.splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            out.append(json.dumps(d))
        return "\\n".join(out)
    locked_rmw(path, _merge)
'''
        got = _scan(src)
        assert got.get("outer._merge") == "RISK"

    def test_the_split_helper_shape(self):
        # THE r4 blind spot: read-helper + write-helper + orchestrator,
        # all three top-level. None of them individually holds both
        # splitlines and a write marker.
        src = '''
def _read(path):
    rows = {}
    for line in path.read_text().splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        rows[d["id"]] = d
    return rows

def _write(path, rows):
    atomic_write(path, "\\n".join(json.dumps(r) for r in rows.values()))

def bump(path, key):
    rows = _read(path)
    rows[key] = {"id": key}
    _write(path, rows)
'''
        got = _scan(src)
        assert got.get("_read") == "RISK", (
            "the read-helper must be flagged: a caller writes back what it "
            f"returns, so its drop is durable — got {got}")

    def test_a_read_only_loop_is_not_flagged(self):
        # Negative control: same drop, no write-back anywhere. Recoverable
        # (the bytes stay on disk) and therefore out of THIS scanner's
        # scope — test_no_silent_drop.py owns the silence half.
        src = '''
def load(path):
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
'''
        assert _scan(src) == {}

    def test_a_taint_refusing_rewrite_reads_as_ok(self):
        # Negative control for the verdict: the fixed shape.
        src = '''
def rewrite(path, key):
    out = []
    for line in path.read_text().splitlines():
        try:
            if loads_clean(line).get("id") == key:
                continue
        except Exception:
            pass
        out.append(line)
    atomic_write(path, "\\n".join(out))
'''
        assert _scan(src) == {"rewrite": "OK"}


class TestTheScannerStillSeesTheRealFixedSurfaces:
    def test_the_live_skills_helpers_are_visible(self):
        # Regression pin for the r4 finding: these three were invisible
        # (not OK, not RISK — absent) before the call-graph leg landed.
        src = (Path(__file__).parent.parent / "src" / "skills.py").read_text(
            encoding="utf-8", errors="surrogateescape")
        got = _scan(src)
        assert got.get("_read_skill_stats") == "OK"
        assert got.get("_save_skills") == "OK"
        assert got.get("save_skill") == "OK"


def test_the_triage_manifest_matches_the_live_scan():
    """A new RISK site must not quietly inherit "already triaged".

    Adversarial round 2026-08-20 (Experimentalist, accepted): the triage
    record shipped aggregate categories with examples, so "every false
    positive has a reason written down" was not reproducible — you could not
    look a site up. scripts/triage_manifest.py is the mapping; this pins that
    it stays true as the tree moves.
    """
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).parent.parent
    proc = subprocess.run(
        [sys.executable, "scripts/triage_manifest.py", "--check"],
        cwd=repo, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        "triage manifest drifted from the scanner:\n" + proc.stdout + proc.stderr)


def _manifest():
    """Import scripts/triage_manifest.py without putting scripts/ on sys.path."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parent.parent / "scripts" / "triage_manifest.py"
    spec = importlib.util.spec_from_file_location("_triage_manifest", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_drift_gate_catches_a_brand_new_risk_site():
    """A green baseline proves nothing about a gate. Adversarial r2
    (2026-08-20): the committed test only checked that --check passes today,
    so `main()` always returning 0 would still have passed it."""
    tm = _manifest()
    live = (set(tm.SITES) - tm.FIXED) | {"brand_new.py:some_rewrite"}
    untriaged, stale, regressed, vanished = tm.compare(live)
    assert untriaged == ["brand_new.py:some_rewrite"]
    assert not stale and not regressed and not vanished


def test_the_drift_gate_catches_a_stale_manifest_entry():
    tm = _manifest()
    live = set(tm.SITES) - tm.FIXED
    dropped = sorted(live)[0]
    untriaged, stale, regressed, vanished = tm.compare(live - {dropped})
    assert stale == [dropped]
    assert not untriaged and not regressed and not vanished


def test_the_drift_gate_catches_a_fixed_site_turning_destructive_again():
    """The one the gate originally missed: a resurfaced FIXED site was
    neither untriaged (it is in SITES) nor stale (FIXED exempted it), so
    re-introducing the exact destructive rewrite passed silently."""
    tm = _manifest()
    resurfaced = "interrupt.py:poll"
    assert resurfaced in tm.FIXED, "fixture drifted"
    live = (set(tm.SITES) - tm.FIXED) | {resurfaced}
    untriaged, stale, regressed, vanished = tm.compare(live)
    assert regressed == [resurfaced]
    assert not untriaged and not stale and not vanished


@pytest.mark.parametrize("live_extra,expect", [
    ({"brand_new.py:some_rewrite"}, "UNTRIAGED"),
    ({"interrupt.py:poll"}, "REGRESSED"),
])
def test_the_check_verb_itself_exits_nonzero(monkeypatch, capsys, live_extra, expect):
    """Pin the GATE, not just its arithmetic.

    Adversarial r3 (2026-08-20, 4 lenses): every drift test called
    `compare()` directly, and the only test that ran the executable asserted
    the CLEAN baseline exits 0. So `main()`'s `return 1` mutated to `return
    0` passed the whole file — `compare()` stays correct while CI is told
    green. That is the asserted-the-helper-not-the-flow shape our own
    watch-list names, in the test written to close a gate.
    """
    import sys as _sys

    tm = _manifest()
    live = (set(tm.SITES) - tm.FIXED) | live_extra
    monkeypatch.setattr(tm, "_scan", lambda: (live, live | tm.FIXED))
    monkeypatch.setattr(_sys, "argv", ["triage_manifest.py", "--check"])

    rc = tm.main()

    assert rc == 1, "the drift gate reported success on a drifted tree"
    assert expect in capsys.readouterr().out


def test_the_check_verb_exits_zero_on_a_clean_scan(monkeypatch, capsys):
    """The negative control for the pin above — it must still be able to pass."""
    import sys as _sys

    tm = _manifest()
    live = set(tm.SITES) - tm.FIXED
    monkeypatch.setattr(tm, "_scan", lambda: (live, live | tm.FIXED))
    monkeypatch.setattr(_sys, "argv", ["triage_manifest.py", "--check"])
    assert tm.main() == 0
    assert "all triaged" in capsys.readouterr().out


class TestTheScannerSeesTheFramingThisArcConvertedTo:
    """Adversarial r3 (2026-08-20, Expert QA — the sharpest finding of the
    round): the scanner only matched `.splitlines()`, and this arc CONVERTED
    every site it hardened to `.split("\\n")` (splitlines also breaks on
    U+2028/U+2029, legal inside a JSON string). So the fix walked its own
    subject out of the detector's field of view: reverting `interrupt.poll`
    to the exact destructive shape produced ZERO hits, and the drift gate's
    `regressed` check — whose entire job is to catch that — could never fire.
    """

    def test_a_split_lf_rewrite_that_drops_is_found(self):
        src = (
            "def rewrite(path):\n"
            "    out = []\n"
            "    for line in path.read_text().split(\"\\n\"):\n"
            "        try:\n"
            "            out.append(json.loads(line))\n"
            "        except Exception:\n"
            "            continue\n"
            "    atomic_write(path, dumps(out))\n"
        )
        assert _scan(src).get("rewrite") == "RISK"

    def test_the_real_regression_at_a_fixed_site_is_found(self):
        """The literal one: revert poll's taint-refusing parse and the site
        must come back as RISK, or `regressed` is decoration."""
        from pathlib import Path

        src = (Path(__file__).parent.parent / "src" / "interrupt.py").read_text(
            encoding="utf-8", errors="surrogateescape")
        assert _scan(src).get("poll") == "OK", "fixture drifted — poll is not clean"
        assert _scan(src.replace("_loads_clean(raw)", "json.loads(raw)")).get("poll") \
            == "RISK", "a reverted poll is invisible to the scanner"

    def test_a_split_lf_read_with_no_write_back_is_not_flagged(self):
        """The negative control for the new framing leg: `split("\\n")` on its
        own must not start reporting every reader in the tree. Note what is
        NOT claimed — the scanner does not detect the DROP, only framing +
        write-back (see its docstring); that is why 64 of the first 70 sites
        were false positives."""
        src = (
            "def read(path):\n"
            "    out = []\n"
            "    for line in path.read_text().split(\"\\n\"):\n"
            "        try:\n"
            "            out.append(json.loads(line))\n"
            "        except Exception:\n"
            "            continue\n"
            "    return out\n"
        )
        assert "read" not in _scan(src)


def test_the_drift_gate_catches_a_fixed_site_leaving_the_scanners_view():
    """The failure `regressed` structurally cannot see, and the one this arc
    actually shipped: a fixed site does not have to turn RISK to stop being
    watched — it can simply stop being reported at all, and then
    `live & FIXED` is empty forever and the gate says green. The arc's own
    `splitlines()` -> `split("\\n")` conversion did exactly that to all six of
    its fixed sites (adversarial r3). Watching a site means being able to see
    it."""
    tm = _manifest()
    live = set(tm.SITES) - tm.FIXED
    gone = sorted(tm.FIXED)[0]
    seen = live | (tm.FIXED - {gone})
    untriaged, stale, regressed, vanished = tm.compare(live, seen)
    assert vanished == [gone]
    assert not untriaged and not regressed


def test_the_vanished_leg_is_quiet_when_every_fixed_site_is_still_visible():
    """Negative control — a gate that always fires is not a gate."""
    tm = _manifest()
    live = set(tm.SITES) - tm.FIXED
    untriaged, stale, regressed, vanished = tm.compare(live, live | tm.FIXED)
    assert not (untriaged or stale or regressed or vanished)


def test_the_check_verb_exits_nonzero_on_a_vanished_fixed_site(monkeypatch, capsys):
    """And the GATE, not just its arithmetic — the r3 lesson applied to the
    leg r4 added."""
    import sys as _sys

    tm = _manifest()
    live = set(tm.SITES) - tm.FIXED
    gone = sorted(tm.FIXED)[0]
    monkeypatch.setattr(tm, "_scan", lambda: (live, live | (tm.FIXED - {gone})))
    monkeypatch.setattr(_sys, "argv", ["triage_manifest.py", "--check"])
    assert tm.main() == 1
    assert "VANISHED" in capsys.readouterr().out


class TestTheScannerSeesEveryLineFramingIdiom:
    """Adversarial r4 (3 lenses): r3's `frames_lines` took only positional
    `split("\\n")`. Each idiom it missed is one more way for a destructive
    rewrite to be invisible — and `split(b"\\n")` was never hypothetical,
    `src/jsonl_utils.py` itself uses it."""

    def _destructive(self, framing: str) -> str:
        return (
            "def rewrite(path):\n"
            "    out = []\n"
            f"    for line in {framing}:\n"
            "        try:\n"
            "            out.append(json.loads(line))\n"
            "        except Exception:\n"
            "            continue\n"
            "    atomic_write(path, dumps(out))\n"
        )

    @pytest.mark.parametrize("framing", [
        'path.read_text().splitlines()',
        'path.read_text().split("\\n")',
        'path.read_text().split(sep="\\n")',
        'path.read_bytes().split(b"\\n")',
        'path.open().readlines()',
    ])
    def test_each_framing_idiom_is_found(self, framing):
        assert _scan(self._destructive(framing)).get("rewrite") == "RISK", framing

    @pytest.mark.parametrize("framing", [
        'path.read_text().split(",")',
        'path.read_text().split(sep=",")',
    ])
    def test_a_non_line_separator_is_not_framing(self, framing):
        """Negative control: a CSV split is not JSONL framing."""
        assert "rewrite" not in _scan(self._destructive(framing)), framing


class TestTheScannerSeesTheTwoIdiomsR5Found:
    """Adversarial r5 (2026-08-20, 3 lenses, probed): r4's `frames_lines`
    still could not see the two shapes a routine refactor of any hardened
    site produces — hoisting the separator into a local, and moving from
    `.readlines()` to plain iteration over an open handle. Both returned
    ZERO hits against a genuinely destructive rewrite. The `vanished` gate
    can only report what the scanner can see, so a blind spot here is a
    blind spot in the gate too."""

    DESTRUCTIVE_BODY = (
        "        try:\n"
        "            json.loads(line)\n"
        "        except Exception:\n"
        "            continue\n"
        "        out.append(line)\n"
        '    atomic_write(path, "\\n".join(out))\n'
    )

    @pytest.mark.parametrize("label,head", [
        ("file iteration via with/as",
         "def rewrite(path):\n    out = []\n    with path.open() as fh:\n"
         "        for line in fh:\n"),
        ("file iteration via open()",
         "def rewrite(path):\n    out = []\n    with open(path) as fh:\n"
         "        for line in fh:\n"),
        ("iterating the open() call itself",
         "def rewrite(path):\n    out = []\n    for line in open(path):\n"),
        ("separator hoisted to a local",
         'def rewrite(path):\n    out = []\n    sep = "\\n"\n'
         "    for line in path.read_text().split(sep):\n"),
        ("separator that cannot be resolved",
         "def rewrite(path, sep):\n    out = []\n"
         "    for line in path.read_text().split(sep):\n"),
    ])
    def test_each_is_found(self, label, head):
        body = self.DESTRUCTIVE_BODY
        if "with " in head:                      # keep the indentation legal
            body = "".join("    " + ln if ln.strip() else ln
                           for ln in body.splitlines(keepends=True)[:-1]) \
                   + self.DESTRUCTIVE_BODY.splitlines(keepends=True)[-1]
        assert _scan(head + body).get("rewrite") == "RISK", label

    @pytest.mark.parametrize("label,src", [
        ("a resolved non-newline local",
         'def rewrite(path):\n    out = []\n    sep = ","\n'
         "    for cell in path.read_text().split(sep):\n"
         "        try:\n            json.loads(cell)\n"
         "        except Exception:\n            continue\n"
         "        out.append(cell)\n"
         '    atomic_write(path, ",".join(out))\n'),
        ("shlex.split is shell words, not store lines",
         "def rewrite(path, cmd):\n    out = []\n"
         "    for word in shlex.split(cmd):\n"
         "        try:\n            json.loads(word)\n"
         "        except Exception:\n            continue\n"
         "        out.append(word)\n"
         '    atomic_write(path, " ".join(out))\n'),
        ("iterating a plain list is not framing",
         "def rewrite(path, rows):\n    out = []\n"
         "    for line in rows:\n"
         "        try:\n            json.loads(line)\n"
         "        except Exception:\n            continue\n"
         "        out.append(line)\n"
         '    atomic_write(path, "\\n".join(out))\n'),
    ])
    def test_the_negative_controls_stay_quiet(self, label, src):
        assert "rewrite" not in _scan(src), label


class TestTheOkVerdictIsNotBoughtByMentioningTheGuard:
    """Adversarial r5 (Architect, probed): the OK verdict was a substring
    test over the whole function, so a rewrite that parses every line with
    bare `json.loads` and merely MENTIONS `loads_clean` somewhere reported
    OK — which the manifest's `vanished` leg then counts as a watched,
    healthy site. A function still parsing with the unguarded call has not
    been cleared, whatever else it mentions."""

    def test_an_unrelated_mention_does_not_clear_a_bare_parse(self):
        src = ('def poll(path):\n'
               '    loads_clean("unrelated")\n'
               '    out = []\n'
               '    for line in path.read_text().split("\\n"):\n'
               '        try:\n            json.loads(line)\n'
               '        except Exception:\n            continue\n'
               '        out.append(line)\n'
               '    atomic_write(path, "\\n".join(out))\n')
        assert _scan(src).get("poll") == "RISK"

    def test_a_genuinely_guarded_rewrite_is_still_ok(self):
        """The negative control — otherwise the rule is just "everything is
        RISK", which tells a triager nothing."""
        src = ('def poll(path):\n'
               '    out = []\n'
               '    for line in path.read_text().split("\\n"):\n'
               '        try:\n            loads_clean(line)\n'
               '        except Exception:\n            pass\n'
               '        out.append(line)\n'
               '    atomic_write(path, "\\n".join(out))\n')
        assert _scan(src).get("poll") == "OK"


class TestTheOkVerdictSurvivesAnImportRefactor:
    """Adversarial r6 (2026-08-20, 4 lenses, probed): r5's "no bare
    json.loads" rule matched one spelling, so `import json as j` and
    `from json import loads` walked past it and an unguarded destructive
    rewrite was certified OK for mentioning `loads_clean` elsewhere. Chasing
    spellings is the losing half of the trade: the rule is now that ANY
    `loads` call which is not the clean wrapper counts as unguarded."""

    BODY = ('    out = []\n'
            '    for line in path.read_text().split("\\n"):\n'
            '        try:\n            %s(line)\n'
            '        except Exception:\n            continue\n'
            '        out.append(line)\n'
            '    atomic_write(path, "\\n".join(out))\n')

    @pytest.mark.parametrize("label,head,parse", [
        ("module alias", "import json as j\n", "j.loads"),
        ("direct import", "from json import loads\n", "loads"),
        ("aliased direct import", "from json import loads as parse\n", "parse_loads"),
        ("the original spelling", "import json\n", "json.loads"),
        ("another parser entirely", "import yaml\n", "yaml.loads"),
    ])
    def test_an_unguarded_parse_is_never_ok(self, label, head, parse):
        src = head + 'def rewrite(path):\n    loads_clean("unrelated")\n' \
              + self.BODY % parse
        assert _scan(src).get("rewrite") == "RISK", label

    @pytest.mark.parametrize("parse", ["loads_clean", "_loads_clean"])
    def test_the_clean_wrapper_still_earns_ok(self, parse):
        """Negative control — a rule that never says OK tells a triager
        nothing."""
        src = "def rewrite(path):\n" + self.BODY % parse
        assert _scan(src).get("rewrite") == "OK"


class TestASeparatorIsOnlyProvenWhenNothingElseTouchesIt:
    """Adversarial r6 (2 lenses, probed): r5 resolved a name separator by
    keeping the LAST assignment found in AST-walk order, which is not control
    flow. A conditional binding and a plain later reassignment each made a
    live JSONL rewrite vanish from the scan — the exact must-detect shape r5
    had just added."""

    BODY = ('    out = []\n'
            '    for line in path.read_text().split(sep):\n'
            '        try:\n            json.loads(line)\n'
            '        except Exception:\n            continue\n'
            '        out.append(line)\n'
            '    atomic_write(path, "\\n".join(out))\n')

    @pytest.mark.parametrize("label,src", [
        ("conditional binding",
         'def rewrite(path, newline):\n    if newline:\n        sep = "\\n"\n'
         '    else:\n        sep = ","\n' + BODY),
        ("reassigned after use",
         'def rewrite(path):\n    sep = "\\n"\n' + BODY + '    sep = ","\n'),
        ("bound from a call",
         'def rewrite(path):\n    sep = pick_separator()\n' + BODY),
        ("a parameter",
         "def rewrite(path, sep):\n" + BODY),
    ])
    def test_an_unproven_separator_is_framing(self, label, src):
        assert _scan(src).get("rewrite") == "RISK", label

    def test_one_unconditional_non_newline_binding_still_buys_silence(self):
        """Negative control: this is the only shape that proves it."""
        src = 'def rewrite(path):\n    sep = ","\n' + self.BODY
        assert "rewrite" not in _scan(src)
