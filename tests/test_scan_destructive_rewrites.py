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
        # The import is part of the fixture: adversarial r8 removed the
        # "conventional name with no visible import" fallback, because it
        # trusted `from untrusted_parser import loads_clean` on spelling.
        src = '''
from jsonl_utils import loads_clean
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
    live = (set(tm.SITES) - tm.FIXED - set(tm.MOVED)) | {"brand_new.py:some_rewrite"}
    untriaged, stale, regressed, vanished, blind, resurfaced = tm.compare(live)
    assert untriaged == ["brand_new.py:some_rewrite"]
    assert not stale and not regressed and not vanished and not resurfaced


def test_the_drift_gate_catches_a_stale_manifest_entry():
    tm = _manifest()
    live = set(tm.SITES) - tm.FIXED - set(tm.MOVED)
    # not a MOVED site: those are exempt from `stale` on purpose (r10), and
    # picking one would test the exemption instead of the leg.
    dropped = sorted(live - set(tm.MOVED))[0]
    untriaged, stale, regressed, vanished, blind, resurfaced = tm.compare(live - {dropped})
    assert stale == [dropped]
    assert not untriaged and not regressed and not vanished and not resurfaced


def test_the_drift_gate_catches_a_fixed_site_turning_destructive_again():
    """The one the gate originally missed: a resurfaced FIXED site was
    neither untriaged (it is in SITES) nor stale (FIXED exempted it), so
    re-introducing the exact destructive rewrite passed silently."""
    tm = _manifest()
    back = "interrupt.py:poll"
    assert back in tm.FIXED, "fixture drifted"
    live = (set(tm.SITES) - tm.FIXED - set(tm.MOVED)) | {back}
    untriaged, stale, regressed, vanished, blind, resurfaced = tm.compare(live)
    assert regressed == [back]
    # poll is also a MOVED site, so its outer name coming back fires the
    # r11 resurfaced leg too — both are true and both should say so.
    assert resurfaced == [back]
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
    monkeypatch.setattr(tm, "_scan", lambda: (live, live | (tm.FIXED - set(tm.MOVED))))
    monkeypatch.setattr(_sys, "argv", ["triage_manifest.py", "--check"])

    rc = tm.main()

    assert rc == 1, "the drift gate reported success on a drifted tree"
    assert expect in capsys.readouterr().out


def test_the_check_verb_exits_zero_on_a_clean_scan(monkeypatch, capsys):
    """The negative control for the pin above — it must still be able to pass."""
    import sys as _sys

    tm = _manifest()
    live = set(tm.SITES) - tm.FIXED - set(tm.MOVED)
    monkeypatch.setattr(tm, "_scan", lambda: (live, live | (tm.FIXED - set(tm.MOVED))))
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
        # `poll._mark_applied`, not `poll`: since r10 the scanner reads one
        # lexical scope, so the site is the closure that frames the lines —
        # which is where poll's rewrite has always lived. The manifest's
        # MOVED entry claims exactly this, and this test is what makes the
        # claim falsifiable rather than a comment.
        site = "poll._mark_applied"
        assert _scan(src).get(site) == "OK", "fixture drifted — poll is not clean"
        assert _scan(src.replace("_loads_clean(raw)", "json.loads(raw)")).get(site) \
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
    live = set(tm.SITES) - tm.FIXED - set(tm.MOVED)
    gone = sorted(tm.FIXED - set(tm.MOVED))[0]
    seen = live | (tm.FIXED - set(tm.MOVED) - {gone})
    untriaged, stale, regressed, vanished, blind, resurfaced = tm.compare(live, seen)
    assert vanished == [gone]
    assert not untriaged and not regressed


def test_the_vanished_leg_is_quiet_when_every_fixed_site_is_still_visible():
    """Negative control — a gate that always fires is not a gate."""
    tm = _manifest()
    live = set(tm.SITES) - tm.FIXED - set(tm.MOVED)
    untriaged, stale, regressed, vanished, blind, resurfaced = tm.compare(live, live | (tm.FIXED - set(tm.MOVED)))
    assert not (untriaged or stale or regressed or vanished or blind
                or resurfaced)


def test_the_check_verb_exits_nonzero_on_a_vanished_fixed_site(monkeypatch, capsys):
    """And the GATE, not just its arithmetic — the r3 lesson applied to the
    leg r4 added."""
    import sys as _sys

    tm = _manifest()
    live = set(tm.SITES) - tm.FIXED - set(tm.MOVED)
    gone = sorted(tm.FIXED - set(tm.MOVED))[0]
    monkeypatch.setattr(tm, "_scan", lambda: (live, live | (tm.FIXED - set(tm.MOVED) - {gone})))
    monkeypatch.setattr(_sys, "argv", ["triage_manifest.py", "--check"])
    assert tm.main() == 1
    assert "VANISHED" in capsys.readouterr().out


class TestTheMovedExemptionKeepsPayingForItself:
    """Adversarial r10 made the scanner lexical, which moved nine sites'
    framing into the closure that owns it. `MOVED` excuses those from
    `stale`/`vanished` — and an exemption with no counter-check is the
    one-directional hole `regressed` and `vanished` were both written
    against. So the excuse is only good while the scanner can still see the
    site named as the new home."""

    def test_a_moved_sites_new_home_leaving_the_scan_is_caught(self):
        tm = _manifest()
        twin = next(t for t in tm.MOVED.values() if t is not None)
        live = set(tm.SITES) - tm.FIXED - set(tm.MOVED)
        seen = (live | (tm.FIXED - set(tm.MOVED))) - {twin}
        untriaged, stale, regressed, vanished, blind, resurfaced = tm.compare(live, seen)
        assert blind == [twin]

    def test_the_moved_sites_themselves_are_not_reported_stale(self):
        """The negative control: they are absent from the live scan BY
        DESIGN, and reporting them would make the gate cry wolf nine times
        on a clean tree."""
        tm = _manifest()
        live = (set(tm.SITES) - tm.FIXED) - set(tm.MOVED)
        untriaged, stale, regressed, vanished, blind, resurfaced = tm.compare(
            live, live | (tm.FIXED - set(tm.MOVED)))
        assert not (stale or vanished or blind or resurfaced)

    def test_the_check_verb_exits_nonzero_on_a_blind_moved_site(
            self, monkeypatch, capsys):
        """And the GATE, not just its arithmetic."""
        import sys as _sys

        tm = _manifest()
        twin = next(t for t in tm.MOVED.values() if t is not None)
        live = set(tm.SITES) - tm.FIXED - set(tm.MOVED)
        monkeypatch.setattr(
            tm, "_scan", lambda: (live, (live | (tm.FIXED - set(tm.MOVED))) - {twin}))
        monkeypatch.setattr(_sys, "argv", ["triage_manifest.py", "--check"])
        assert tm.main() == 1
        assert "BLIND" in capsys.readouterr().out

    def test_every_named_twin_is_actually_in_the_live_scan(self):
        """The manifest's own claim, checked against the real scanner: each
        MOVED entry asserts a surface is still watched somewhere. If that is
        prose rather than fact, the exemption is a deletion with a comment
        on it."""
        tm = _manifest()
        _live, seen = tm._scan()
        missing = [t for t in tm.MOVED.values() if t is not None and t not in seen]
        assert not missing, missing


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
        src = ('from jsonl_utils import loads_clean\n'
               'def poll(path):\n'
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
        # r6 wrote this row as `parse_loads` — a name that appears NOWHERE in
        # the fixture's imports and happens to match the old suffix
        # heuristic. It passed for the wrong reason: adversarial r7 probed
        # the real shape, `parse(line)`, and got OK. A must-detect fixture
        # that tests the heuristic instead of the production shape is the
        # exact failure this repo's own rule warns about.
        ("aliased direct import", "from json import loads as parse\n", "parse"),
        ("assignment alias", "import json\n", "PARSE_ALIAS"),
        ("a trusted-looking alias of the raw parser",
         "from json import loads as _loads_clean\n", "_loads_clean"),
        ("the original spelling", "import json\n", "json.loads"),
        ("another parser entirely", "import yaml\n", "yaml.loads"),
    ])
    def test_an_unguarded_parse_is_never_ok(self, label, head, parse):
        prelude = "    PARSE_ALIAS = json.loads\n" if parse == "PARSE_ALIAS" else ""
        # The guard mention has to be a PROVEN one — r8 removed the
        # spelling fallback, so an unimported `loads_clean("unrelated")` no
        # longer earns clean status and the fixture stopped exercising the
        # unguarded-parse branch at all (it read RISK for the other reason).
        # A must-detect fixture that passes for the wrong reason is the
        # failure this repo's own rule warns about; the sweep found it.
        src = head + "from jsonl_utils import loads_clean\n" \
              + 'def rewrite(path):\n    loads_clean("unrelated")\n' \
              + prelude + self.BODY % parse
        assert _scan(src).get("rewrite") == "RISK", label

    @pytest.mark.parametrize("label,src", [
        ("imported from jsonl_utils",
         "from jsonl_utils import loads_clean\ndef rewrite(path):\n"),
        ("imported under an alias",
         "from jsonl_utils import loads_clean as _lc\ndef rewrite(path):\n"),
        ("called through the module",
         "import jsonl_utils\ndef rewrite(path):\n"),
    ])
    def test_a_proven_clean_binding_earns_ok(self, label, src):
        call = {"imported from jsonl_utils": "loads_clean",
                "imported under an alias": "_lc",
                "called through the module": "jsonl_utils.loads_clean"}[label]
        assert _scan(src + self.BODY % call).get("rewrite") == "OK", label

    @pytest.mark.parametrize("parse", ["loads_clean", "_loads_clean"])
    def test_the_clean_wrapper_still_earns_ok(self, parse):
        """Negative control — a rule that never says OK tells a triager
        nothing. The import is what proves it; see the r8 case below."""
        head = f"from jsonl_utils import loads_clean as {parse}\n" \
            if parse == "_loads_clean" else "from jsonl_utils import loads_clean\n"
        assert _scan(head + "def rewrite(path):\n"
                     + self.BODY % parse).get("rewrite") == "OK"

    @pytest.mark.parametrize("label,head", [
        ("no import at all", ""),
        ("imported from somewhere else",
         "from untrusted_parser import loads_clean\n"),
        ("imported from a module that merely ends in the right word",
         "from vendor.not_jsonl_utils import loads_clean\n"),
    ])
    def test_the_conventional_name_alone_does_not_earn_ok(self, label, head):
        """Adversarial r8 (3 lenses, probed). r7 kept a fallback for the
        conventional spelling, added so its own fixtures would pass, and it
        trusted any `loads_clean` — including one imported from an arbitrary
        module. A name is not provenance."""
        assert _scan(head + "def rewrite(path):\n"
                     + self.BODY % "loads_clean").get("rewrite") == "RISK", label


class TestAModuleWideProofDoesNotSurviveALocalRebinding:
    """Adversarial r8 (4 lenses, probed): parser identity was collected
    module-wide, so a function that shadowed the imported wrapper — with a
    parameter, a default argument, or a plain local assignment — parsed with
    the raw parser while the scanner read the module-level import and said
    OK. A binding that is not proven clean IN THIS SCOPE cannot inherit the
    module's proof."""

    BODY = ('    out = []\n'
            '    for line in path.read_text().split("\\n"):\n'
            '        try:\n            loads_clean(line)\n'
            '        except Exception:\n            continue\n'
            '        out.append(line)\n'
            '    atomic_write(path, "\\n".join(out))\n')

    HEAD = "from jsonl_utils import loads_clean\nimport json\n"

    @pytest.mark.parametrize("label,sig,prelude", [
        ("a parameter shadows it", "def rewrite(path, loads_clean):\n", ""),
        ("a default argument shadows it",
         "def rewrite(path, loads_clean=json.loads):\n", ""),
        ("a local assignment rebinds it", "def rewrite(path):\n",
         "    loads_clean = json.loads\n"),
        ("a lambda rebinds it", "def rewrite(path):\n",
         "    loads_clean = lambda s: json.loads(s)\n"),
    ])
    def test_a_shadowed_wrapper_is_not_proof(self, label, sig, prelude):
        assert _scan(self.HEAD + sig + prelude
                     + self.BODY).get("rewrite") == "RISK", label

    @pytest.mark.parametrize("label,sig,prelude", [
        ("the function imports it itself", "def rewrite(path):\n",
         "    from jsonl_utils import loads_clean\n"),
        ("the function aliases the module-level import",
         "def rewrite(path):\n", "    loads_clean = _module_level\n"),
    ])
    def test_a_local_proof_is_still_a_proof(self, label, sig, prelude):
        """The must-detect other half. Half this codebase imports the
        wrapper INSIDE the function (doctor.py, gc_memory.py); reading that
        import as a shadow would turn every one of them RISK — which is how
        a strictness change stops being a signal."""
        head = "from jsonl_utils import loads_clean as _module_level\n"
        assert _scan(head + sig + prelude
                     + self.BODY).get("rewrite") == "OK", label


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


class TestEveryBindingFormCountsAsABinding:
    """Adversarial r7 (3 lenses, probed): r6's "exactly one binding proves
    it" guard counted only `ast.Assign`. `sep: str = "\\n"`, `sep += "\\n"`
    and `(sep := "\\n")` were invisible, so a function binding a comma once
    and a newline by any other form was "proven" non-framing and vanished
    from the scan entirely — neither RISK nor OK, which is worse than either
    because the drift gate can only report what the scanner can see."""

    BODY = ('    out = []\n    for line in path.read_text().split(sep):\n'
            '        try:\n            json.loads(line)\n'
            '        except Exception:\n            continue\n'
            '        out.append(line)\n    atomic_write(path, "x")\n')

    @pytest.mark.parametrize("label,binding", [
        ("annotated", '    sep = ","\n    sep: str = "\\n"\n'),
        ("augmented", '    sep = ","\n    sep += "\\n"\n'),
        ("walrus", '    sep = ","\n    if (sep := "\\n"):\n        pass\n'),
        ("loop target", '    sep = ","\n    for sep in seps:\n        pass\n'),
        ("with target", '    sep = ","\n    with opened() as sep:\n        pass\n'),
        ("plain rebinding", '    sep = ","\n    sep = "\\n"\n'),
    ])
    def test_a_second_binding_makes_the_separator_unproven(self, label, binding):
        src = "import json\ndef rewrite(path, seps):\n" + binding + self.BODY
        assert _scan(src).get("rewrite") == "RISK", label

    def test_one_binding_is_still_a_proof(self):
        """Negative control — otherwise every variable separator is RISK and
        the resolution logic may as well not exist."""
        src = 'import json\ndef rewrite(path):\n    sep = ","\n' + self.BODY
        assert "rewrite" not in _scan(src)


class TestABindingIsAnythingPythonBinds:
    """Adversarial r8 (2 lenses, probed). r7 enumerated binding NODE TYPES
    and r8 found the two it had not thought of — a tuple target and a
    `match` capture — each of which made a live JSONL rewrite vanish from
    the scan entirely (neither RISK nor OK), which is the same
    disappearance this arc has now paid for three times. Enumerating forms
    is a denylist; the census counts Store-context names instead."""

    BODY = ('    out = []\n'
            '    for line in path.read_text().split(sep):\n'
            '        try:\n            json.loads(line)\n'
            '        except Exception:\n            continue\n'
            '        out.append(line)\n'
            '    atomic_write(path, "\\n".join(out))\n')

    @pytest.mark.parametrize("label,head", [
        ("tuple target",
         'def rewrite(path, nl):\n    sep = ","\n'
         '    if nl:\n        sep, _unused = "\\n", 0\n'),
        ("list target",
         'def rewrite(path, nl):\n    sep = ","\n'
         '    if nl:\n        [sep, _unused] = ["\\n", 0]\n'),
        ("starred target",
         'def rewrite(path, nl):\n    sep = ","\n'
         '    if nl:\n        sep, *_rest = "\\n", 0\n'),
        ("match capture",
         'def rewrite(path, config):\n    sep = ","\n'
         '    match config:\n        case {"separator": sep}:\n            pass\n'),
        ("match rest capture",
         'def rewrite(path, config):\n    sep = ","\n'
         '    match config:\n        case {"x": 1, **sep}:\n            pass\n'),
        ("except alias",
         'def rewrite(path, nl):\n    sep = ","\n'
         '    try:\n        nl()\n    except Exception as sep:\n        pass\n'),
        ("a local import",
         'def rewrite(path, nl):\n    sep = ","\n'
         '    if nl:\n        from constants import sep\n'),
    ])
    def test_a_second_binding_of_any_form_means_unresolved(self, label, head):
        assert _scan(head + self.BODY).get("rewrite") == "RISK", label

    def test_one_proven_comma_still_buys_silence(self):
        """The must-detect other half: if every binding form counted as
        unresolved the rule would just be "everything is RISK"."""
        src = 'def rewrite(path):\n    sep = ","\n' + self.BODY
        assert _scan(src).get("rewrite") is None, \
            "a proven non-newline separator is not line framing"


class TestAProofIsScopedAndItsModuleIsNamedExactly:
    """Adversarial r9 (4 lenses, probed) on r8's scope-aware parser
    identity. Three shapes walked past it and one module-name check was
    still a spelling test."""

    BODY = ('    out = []\n'
            '    for line in path.read_text().split("\\n"):\n'
            '        try:\n            %s(line)\n'
            '        except Exception:\n            continue\n'
            '        out.append(line)\n'
            '    atomic_write(path, "\\n".join(out))\n')

    @pytest.mark.parametrize("label,src", [
        ("a module whose last component is spelled right",
         "from vendor.jsonl_utils import loads_clean\ndef rewrite(path):\n"
         "%s"),
        ("the same, imported as a module",
         "import vendor.jsonl_utils as m\ndef rewrite(path):\n%s"),
        ("a nested import re-proving the outer function's raw parameter",
         "import json\ndef rewrite(path, loads_clean=json.loads):\n"
         "    def helper():\n"
         "        from jsonl_utils import loads_clean\n"
         "        return loads_clean\n%s"),
        ("a shadowed module receiver",
         "import jsonl_utils\ndef rewrite(path, jsonl_utils):\n%s"),
        ("a module alias rebound locally",
         "import jsonl_utils as pm\nimport json\n"
         "def rewrite(path):\n    pm = json\n%s"),
    ])
    def test_an_unproven_parser_is_never_ok(self, label, src):
        call = {"a module whose last component is spelled right": "loads_clean",
                "the same, imported as a module": "m.loads_clean",
                "a nested import re-proving the outer function's raw parameter":
                    "loads_clean",
                "a shadowed module receiver": "jsonl_utils.loads_clean",
                "a module alias rebound locally": "pm.loads_clean"}[label]
        got = _scan(src % (self.BODY % call))
        assert got.get("rewrite") == "RISK", (label, got)

    @pytest.mark.parametrize("label,src,call", [
        ("the real module import",
         "from jsonl_utils import loads_clean\ndef rewrite(path):\n%s",
         "loads_clean"),
        ("the real module, called through it",
         "import jsonl_utils\ndef rewrite(path):\n%s",
         "jsonl_utils.loads_clean"),
        ("a function-local import",
         "def rewrite(path):\n    from jsonl_utils import loads_clean\n%s",
         "loads_clean"),
        ("an alias of the real import",
         "from jsonl_utils import loads_clean as _lc\ndef rewrite(path):\n%s",
         "_lc"),
    ])
    def test_a_real_proof_still_earns_ok(self, label, src, call):
        """The must-detect other half — every one of these shapes is in the
        live tree, and a strictness change that turns them RISK stops being
        a signal."""
        got = _scan(src % (self.BODY % call))
        assert got.get("rewrite") == "OK", (label, got)


class TestANameCollisionIsNotEvidence:
    """Adversarial r9 (Skeptic, probed): the call-graph leg indexed
    functions by bare name in a dict, so an unrelated class's `save`
    replaced the one actually called and the destructive helper vanished
    from the scan entirely — neither RISK nor OK, the disappearance this
    arc has now paid for four times."""

    SRC = '''
class A:
    def helper(self, path):
        rows = []
        for line in path.read_text().split("\\n"):
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        return rows
    def save(self, path, text):
        path.write_text(text)
    def rewrite(self, path):
        rows = self.helper(path)
        self.save(path, "x")
%s
'''

    def test_the_destructive_helper_is_still_seen(self):
        other = ('class B:\n    def save(self, path, text):\n'
                 '        return len(text)\n')
        # The scanner qualifies by enclosing FUNCTION, not by class, so
        # the site is reported as `helper` — that naming is what the
        # manifest keys on.
        assert _scan(self.SRC % other).get("helper") == "RISK"

    def test_it_was_seen_without_the_collision_too(self):
        """The control: the finding is the collision, not the shape."""
        assert _scan(self.SRC % "").get("helper") == "RISK"

    def test_the_harmless_twin_coming_FIRST_does_not_poison_the_writer(self):
        """r9 keyed the call graph's cycle detection on `fn.name`, so the
        first `save` evaluated inserted "save" into `seen` and every other
        `save` returned False before its body was read. r9's own fixture put
        the destructive one first and passed; adversarial r10 (3 seats,
        independently probed) reversed the order and the reader vanished
        from the scan entirely — the same disappearance r9 was written to
        fix, one definition-order edit away. Order is not evidence."""
        other = ('class B:\n    def save(self, path, text):\n'
                 '        return len(text)\n')
        assert _scan(other + self.SRC % "").get("helper") == "RISK"


class TestANestedScopeCannotVouchForItsParent:
    """Adversarial r10 (Minimalist + Expert QA, both probed): r9 gave the
    binding census and `_parser_names` lexical scopes and left the two
    scans that USE them walking the whole subtree. So a `loads_clean` call
    — or a re-proving assignment — inside a nested helper that need never
    execute cleared the outer function's verdict, while every line of the
    outer rewrite went through the raw parser."""

    DESTRUCTIVE = (
        "    keep = []\n"
        "    for line in open(path).read().split(\"\\n\"):\n"
        "        if line == \"\":\n"
        "            continue\n"
        "        row = %s(line)\n"
        "        if row.get(\"ok\"):\n"
        "            keep.append(line)\n"
        "%s"
        "    atomic_write(path, \"\\n\".join(keep))\n"
    )

    def test_a_clean_call_in_a_nested_helper_does_not_certify_the_outer(self):
        nested = ("    def unrelated(s):\n"
                  "        return loads_clean(s)\n")
        src = ("from jsonl_utils import loads_clean\n"
               "def rewrite(path, parser):\n" + self.DESTRUCTIVE % ("parser", nested))
        assert _scan(src).get("rewrite") == "RISK"

    def test_the_control_without_the_nested_helper(self):
        src = ("from jsonl_utils import loads_clean\n"
               "def rewrite(path, parser):\n" + self.DESTRUCTIVE % ("parser", ""))
        assert _scan(src).get("rewrite") == "RISK"

    def test_the_outer_calling_it_itself_still_earns_OK(self):
        """The one that must NOT move: a proof in the function's own scope
        is still a proof. Without this the fix could be 'always RISK'."""
        src = ("from jsonl_utils import loads_clean\n"
               "def rewrite(path):\n" + self.DESTRUCTIVE % ("loads_clean", ""))
        assert _scan(src).get("rewrite") == "OK"

    def test_a_nested_reproof_does_not_clear_the_outer_parameter_shadow(self):
        """The `_shadowed` half of the same defect: `def nested():
        loads_clean = clean` re-proved the OUTER function's
        `loads_clean=json.loads` parameter."""
        nested = ("    def nested():\n"
                  "        loads_clean = clean\n"
                  "        return loads_clean\n")
        src = ("import json\n"
               "from jsonl_utils import loads_clean as clean\n"
               "def rewrite(path, loads_clean=json.loads):\n"
               + self.DESTRUCTIVE % ("loads_clean", nested))
        assert _scan(src).get("rewrite") == "RISK"

    def test_the_same_reproof_in_the_functions_OWN_scope_still_counts(self):
        """Negative control: the r8 rule this must not undo — a local
        `X = <module-proved name>` IS a proof when it is in this scope."""
        src = ("import json\n"
               "from jsonl_utils import loads_clean as clean\n"
               "def rewrite(path):\n"
               "    loads_clean = clean\n"
               + self.DESTRUCTIVE % ("loads_clean", ""))
        assert _scan(src).get("rewrite") == "OK"


class TestAProofInsideAGeneratorExpressionProvesNothing:
    """Adversarial r11 (F7): a genexp body is DEFERRED code — `(loads_clean(s)
    for s in ())` never runs a thing, yet the scanner credited its clean
    call to the enclosing function and certified a raw rewrite OK. The rule
    is asymmetric on purpose: clean-in-genexp proves nothing, but
    raw-in-genexp still poisons (if it ever runs, it runs raw), and EAGER
    comprehensions execute at the expression so their proof value stands."""

    GENEXP = '''
from jsonl_utils import loads_clean
def rewrite(path, parser):
    keep = []
    for line in open(path).read().split("\\n"):
        if line == "":
            continue
        row = parser(line)
        if row.get("ok"):
            keep.append(line)
    _unused = (loads_clean(s) for s in ())
    atomic_write(path, "\\n".join(keep))
'''

    def test_an_inert_genexp_cannot_certify_a_raw_rewrite(self):
        assert _scan(self.GENEXP)["rewrite"] == "RISK"

    def test_the_same_call_in_a_list_comprehension_still_counts(self):
        """Negative control: eager comprehensions run where they stand —
        excluding them would turn real hardened code into false RISK."""
        src = self.GENEXP.replace("(loads_clean(s) for s in ())",
                                  "[loads_clean(s) for s in ()]")
        assert _scan(src)["rewrite"] == "OK"

    def test_a_raw_parse_inside_a_genexp_still_poisons(self):
        """The asymmetry's other half: deferred raw is still raw."""
        src = '''
def rewrite(path):
    rows = (json.loads(l) for l in open(path).read().split("\\n") if l != "")
    keep = [l for l in rows]
    atomic_write(path, "x")
'''
        assert _scan(src)["rewrite"] == "RISK"


class TestAJSONDecoderIsAParserTheScannerCanSee:
    """Adversarial r11 (F8): `json.JSONDecoder().decode(line)` is stdlib
    spelling for the same raw parse as `json.loads`, and the scanner did
    not know the name — one rename made a destructive rewrite invisible."""

    def test_a_bound_decoder_marks_the_site_raw(self):
        src = '''
from jsonl_utils import loads_clean
def rewrite(path):
    decoder = json.JSONDecoder()
    keep = []
    for line in open(path).read().split("\\n"):
        if line == "":
            continue
        try:
            row = decoder.decode(line)
        except Exception:
            continue
        keep.append(line)
    _probe = loads_clean('{}')
    atomic_write(path, "\\n".join(keep))
'''
        assert _scan(src)["rewrite"] == "RISK"

    def test_a_direct_decoder_call_is_seen_too(self):
        src = '''
def rewrite(path):
    keep = []
    for line in open(path).read().split("\\n"):
        try:
            row = json.JSONDecoder().decode(line)
        except Exception:
            continue
        keep.append(line)
    atomic_write(path, "\\n".join(keep))
'''
        assert _scan(src)["rewrite"] == "RISK"

    def test_plain_bytes_decode_is_not_a_parse(self):
        """Negative control: `.decode` is also how BYTES become text —
        flagging `raw.decode("utf-8")` would drown the scan in noise."""
        src = '''
from jsonl_utils import loads_clean
def rewrite(path):
    keep = []
    for raw in open(path, "rb").read().split(b"\\n"):
        line = raw.decode("utf-8", errors="surrogateescape")
        if line == "":
            continue
        try:
            row = loads_clean(line)
        except Exception:
            continue
        keep.append(line)
    atomic_write(path, "\\n".join(keep))
'''
        assert _scan(src)["rewrite"] == "OK"


class TestAMovedSiteComingBackIsCaught:
    """Adversarial r11 (F10): `MOVED` excuses a site from `stale` because
    its scan-visible name moved inward — which also excuses its OUTER name
    from ever being questioned if it comes back. `blind` watches the twin
    disappearing; this leg watches the outer name REAPPEARING (someone put
    framing back in the outer scope). The exemption doctrine, fifth
    application: every exemption needs a counter-check that keeps being
    checked."""

    def test_compare_reports_a_pure_resurfacer(self):
        """A MOVED-but-not-FIXED site: only `resurfaced` may fire — this
        isolates the leg from `regressed` (which needs FIXED)."""
        tm = _manifest()
        outer = next(s for s in tm.MOVED if s not in tm.FIXED)
        live = (set(tm.SITES) - tm.FIXED - set(tm.MOVED)) | {outer}
        untriaged, stale, regressed, vanished, blind, resurfaced = \
            tm.compare(live)
        assert resurfaced == [outer]
        assert not (untriaged or stale or regressed or vanished)

    def test_the_twinless_moved_entry_is_covered_by_the_same_leg(self):
        """`llm.py:_run_subprocess_safe` has no inner twin (`None`), so
        `blind` can never speak for it — resurfaced is its ONLY watch."""
        tm = _manifest()
        outer = next(s for s, t in tm.MOVED.items() if t is None)
        live = (set(tm.SITES) - tm.FIXED - set(tm.MOVED)) | {outer}
        *_, resurfaced = tm.compare(live)
        assert resurfaced == [outer]

    def test_the_check_verb_exits_nonzero_on_a_resurfaced_site(
            self, monkeypatch, capsys):
        """And the GATE, not just its arithmetic."""
        import sys as _sys

        tm = _manifest()
        outer = next(s for s in tm.MOVED if s not in tm.FIXED)
        live = (set(tm.SITES) - tm.FIXED - set(tm.MOVED)) | {outer}
        monkeypatch.setattr(tm, "_scan", lambda: (live, live | (tm.FIXED - set(tm.MOVED))))
        monkeypatch.setattr(_sys, "argv", ["triage_manifest.py", "--check"])

        assert tm.main() == 1
        assert "RESURFACED" in capsys.readouterr().out


class TestDecoderProvenanceNotSpelling:
    """Adversarial r12 (all five seats, each with a different spelling):
    the r11 decoder rule matched the literal `JSONDecoder` constructor and
    the literal `decoder.decode(...)` call shape. Provenance, not the
    final call syntax, is what decides — an import alias, an object alias,
    a bound method, an annotated binding and `raw_decode` each carried the
    same raw parse past the scan while an unrelated clean call earned OK."""

    BASE = '''
from jsonl_utils import loads_clean
def rewrite(path):
    %s
    keep = []
    for line in open(path).read().split("\\n"):
        if line == "":
            continue
        try:
            row = %s
        except Exception:
            continue
        keep.append(line)
    _probe = loads_clean('{}')
    atomic_write(path, "\\n".join(keep))
'''

    @pytest.mark.parametrize("setup,call", [
        ("from json import JSONDecoder as Decoder\n    decoder = Decoder()",
         "decoder.decode(line)"),
        ("import json\n    decoder = json.JSONDecoder()\n    alias = decoder",
         "alias.decode(line)"),
        ("import json\n    decoder = json.JSONDecoder()\n    raw = decoder.decode",
         "raw(line)"),
        ("import json\n    decoder: object = json.JSONDecoder()",
         "decoder.decode(line)"),
        ("import json\n    decoder = json.JSONDecoder()",
         "decoder.raw_decode(line)[0]"),
        ("import json\n    (decoder := json.JSONDecoder())",
         "decoder.decode(line)"),
    ], ids=["import-alias", "object-alias", "bound-method", "annassign",
            "raw_decode", "walrus"])
    def test_every_spelling_marks_the_site_raw(self, setup, call):
        assert _scan(self.BASE % (setup, call))["rewrite"] == "RISK"

    def test_an_aliased_bytes_decode_is_still_not_a_parse(self):
        """Negative control: alias machinery must not start flagging
        ordinary text decoding."""
        src = '''
from jsonl_utils import loads_clean
def rewrite(path):
    blob = open(path, "rb").read()
    keep = []
    for raw in blob.split(b"\\n"):
        line = raw.decode("utf-8", errors="surrogateescape")
        if line == "":
            continue
        try:
            row = loads_clean(line)
        except Exception:
            continue
        keep.append(line)
    atomic_write(path, "\\n".join(keep))
'''
        assert _scan(src)["rewrite"] == "OK"


class TestAResurfacerIsCaughtAtAnyVerdict:
    """Adversarial r12 (Skeptic, probed): `resurfaced` intersected the
    RISK-only `live` set with MOVED, but the move's premise — the outer
    name is expected ABSENT from the scan — is falsified by the name
    coming back at ANY verdict. An OK resurfacer means framing returned
    to the outer scope with a superficially clean parse beside it: more
    suspicious, not less."""

    def test_an_ok_resurfacer_fires_the_leg(self):
        tm = _manifest()
        outer = next(s for s in tm.MOVED if s not in tm.FIXED)
        live = set(tm.SITES) - tm.FIXED - set(tm.MOVED)
        seen = (live | (tm.FIXED - set(tm.MOVED))
                | {t for t in tm.MOVED.values() if t}) | {outer}
        *_, resurfaced = tm.compare(live, seen)
        assert resurfaced == [outer]

    def test_the_three_leg_form_still_sees_a_risk_resurfacer(self):
        """seen=None callers offered only `live` — the leg must keep
        firing there rather than going quiet (the fallback, pinned)."""
        tm = _manifest()
        outer = next(s for s in tm.MOVED if s not in tm.FIXED)
        live = (set(tm.SITES) - tm.FIXED - set(tm.MOVED)) | {outer}
        *_, resurfaced = tm.compare(live)
        assert resurfaced == [outer]


class TestProvenanceSurvivesEverySpelling:
    """Adversarial r13 (four seats, one spelling each, all probed): the
    r12 provenance rules still fell to ordinary alias chains — the
    constructor aliased by assignment, a bound method re-aliased, a tuple
    unpacking, the raw parser bound via AnnAssign/walrus (r12 taught
    `_bindings` those forms and `_parser_names` kept its own private
    Assign-only walk), and an instance stored on `self` in a sibling
    method. Provenance is a lattice over ALL binding forms, or it is a
    denylist."""

    BASE = TestDecoderProvenanceNotSpelling.BASE

    @pytest.mark.parametrize("setup,call", [
        ("import json\n    Ctor = json.JSONDecoder\n    decoder = Ctor()",
         "decoder.decode(line)"),
        ("import json\n    Ctor = json.JSONDecoder\n    Other = Ctor\n"
         "    decoder = Other()",
         "decoder.decode(line)"),
        ("import json\n    decoder = json.JSONDecoder()\n"
         "    raw = decoder.decode\n    rebound = raw",
         "rebound(line)"),
        ("import json\n    decoder, _x = json.JSONDecoder(), None",
         "decoder.raw_decode(line)[0]"),
        ("import json\n    parser: object = json.loads",
         "parser(line)"),
        ("import json\n    (parser := json.loads)",
         "parser(line)"),
    ], ids=["ctor-assign-alias", "ctor-chain", "bound-method-chain",
            "tuple-unpack", "parser-annassign", "parser-walrus"])
    def test_every_alias_chain_marks_the_site_raw(self, setup, call):
        assert _scan(self.BASE % (setup, call))["rewrite"] == "RISK"

    def test_an_instance_attribute_carries_provenance_across_methods(self):
        src = '''
import json
from jsonl_utils import loads_clean
class Repairer:
    def __init__(self):
        self.decoder = json.JSONDecoder()
    def rewrite(self, path):
        keep = []
        for line in open(path).read().split("\\n"):
            if line == "":
                continue
            try:
                row = self.decoder.decode(line)
            except Exception:
                continue
            keep.append(line)
        _probe = loads_clean('{}')
        atomic_write(path, "\\n".join(keep))
'''
        assert _scan(src)["rewrite"] == "RISK"

    def test_an_ordinary_self_attribute_decode_stays_invisible(self):
        """Negative control: `self.blob.decode("utf-8")` is text decoding
        on an attribute nobody proved to be a JSONDecoder."""
        src = '''
from jsonl_utils import loads_clean
class T:
    def __init__(self):
        self.blob = b""
    def rewrite(self, path):
        keep = []
        for raw in open(path, "rb").read().split(b"\\n"):
            line = self.blob.decode("utf-8")
            try:
                row = loads_clean(line)
            except Exception:
                continue
            keep.append(line)
        atomic_write(path, "\\n".join(keep))
'''
        assert _scan(src)["rewrite"] == "OK"


# ---------------------------------------------------------------------------
# Adversarial r14
# ---------------------------------------------------------------------------


_R14_BODY = '''
        out = []
        for line in open(path):
            try:
                out.append(self.decoder.decode(line))
            except Exception:
                continue
        atomic_write(path, "".join(out))
'''


class TestProvenanceCrossesEveryClassBoundary:
    """Adversarial r14 (four seats between them, all probed): four ways
    a class-held raw decoder stayed invisible while an unrelated clean
    call earned the method OK — an attribute-held CONSTRUCTOR alias, a
    CLASS-BODY decoder binding, a decoder inherited from a same-module
    BASE class, and a positional-only receiver that left args.args
    empty."""

    def test_attribute_held_ctor_alias(self):
        src = '''
import json
from jsonl_utils import loads_clean
from file_lock import atomic_write

class Repairer:
    def __init__(self):
        self.Ctor = json.JSONDecoder
        self.decoder = self.Ctor()

    def rewrite(self, path):
        loads_clean("{}")
''' + _R14_BODY
        assert _scan(src)["rewrite"] == "RISK"

    def test_class_body_decoder_binding(self):
        src = '''
import json
from jsonl_utils import loads_clean
from file_lock import atomic_write

class Repairer:
    decoder = json.JSONDecoder()

    def rewrite(self, path):
        loads_clean("{}")
''' + _R14_BODY
        assert _scan(src)["rewrite"] == "RISK"

    def test_inherited_decoder(self):
        src = '''
import json
from jsonl_utils import loads_clean
from file_lock import atomic_write

class Base:
    def __init__(self):
        self.decoder = json.JSONDecoder()

class Child(Base):
    def rewrite(self, path):
        loads_clean("{}")
''' + _R14_BODY
        assert _scan(src)["rewrite"] == "RISK"

    def test_grandparent_decoder(self):
        src = '''
import json
from jsonl_utils import loads_clean
from file_lock import atomic_write

class A:
    def __init__(self):
        self.decoder = json.JSONDecoder()

class B(A):
    pass

class C(B):
    def rewrite(self, path):
        loads_clean("{}")
''' + _R14_BODY
        assert _scan(src)["rewrite"] == "RISK"

    def test_positional_only_receiver(self):
        src = '''
import json
from jsonl_utils import loads_clean
from file_lock import atomic_write

class Repairer:
    def __init__(self, /):
        self.decoder = json.JSONDecoder()

    def rewrite(self, path, /):
        loads_clean("{}")
''' + _R14_BODY
        assert _scan(src)["rewrite"] == "RISK"

    def test_negative_control_inherited_bytes_attribute(self):
        """An inherited ordinary-bytes attribute must NOT poison the
        child's `.decode` — the receiver still decides."""
        src = '''
import json
from jsonl_utils import loads_clean
from file_lock import atomic_write

class Base:
    def __init__(self):
        self.blob = b"data"

class Child(Base):
    def rewrite(self, path):
        out = []
        for line in open(path):
            try:
                out.append(self.blob.decode("utf-8"))
            except Exception:
                continue
        loads_clean("{}")
        atomic_write(path, "".join(out))
'''
        assert _scan(src)["rewrite"] == "OK"


class TestDestructuredAliasesAreBindingsToo:
    """Adversarial r14 (Minimalist, probed): `parser, _x = json.loads,
    None` put a Tuple in the target slot and _parser_names rejected it
    one line before _expand_binding could expose the pair — the same
    private-copy-of-a-shared-walk shape as r13."""

    def test_destructured_raw_parser(self):
        src = '''
import json
from jsonl_utils import loads_clean
from file_lock import atomic_write

def rewrite(path):
    parser, _unused = json.loads, None
    loads_clean("{}")
    out = []
    for line in open(path):
        try:
            out.append(parser(line))
        except Exception:
            continue
    atomic_write(path, "".join(out))
'''
        assert _scan(src)["rewrite"] == "RISK"

    def test_negative_control_destructured_clean_parser(self):
        src = '''
from jsonl_utils import loads_clean
from file_lock import atomic_write

def rewrite(path):
    parser, _unused = loads_clean, None
    out = []
    for line in open(path):
        try:
            out.append(parser(line))
        except Exception:
            continue
    atomic_write(path, "".join(out))
'''
        assert _scan(src)["rewrite"] == "OK"


class TestTheClassWalkIsAFixpoint:
    """Adversarial r14 follow-through: provenance chains that SPAN
    methods must resolve regardless of definition order — a single
    in-order pass resolves the easy direction and silently misses the
    reversed one."""

    def test_reversed_order_cross_method_chain(self):
        src = '''
import json
from jsonl_utils import loads_clean
from file_lock import atomic_write

class Repairer:
    def helper(self):
        self.d2 = self.d1

    def __init__(self):
        self.d1 = json.JSONDecoder()

    def rewrite(self, path):
        loads_clean("{}")
        out = []
        for line in open(path):
            try:
                out.append(self.d2.decode(line))
            except Exception:
                continue
        atomic_write(path, "".join(out))
'''
        assert _scan(src)["rewrite"] == "RISK"


class TestAnAliasedBaseIsStillABase:
    """Adversarial r15 (four seats, probed): the r14 class graph matched
    bases against literal ClassDef names only, so `Alias = Base` or the
    generic spelling `Base[str]` (an ast.Subscript) severed decoder
    provenance at the inheritance boundary — a routine spelling turned
    an inherited raw destructive parse scanner-green while an unrelated
    clean call earned the method OK."""

    BASE = '''
import json
from jsonl_utils import loads_clean
from file_lock import atomic_write

class Base:
    def __init__(self):
        self.decoder = json.JSONDecoder()
'''

    CHILD = '''
    def rewrite(self, path):
        loads_clean("{}")
''' + _R14_BODY

    def test_a_name_alias_carries_provenance(self):
        src = self.BASE + "\nAlias = Base\n\nclass Child(Alias):\n" \
            + self.CHILD
        assert _scan(src)["rewrite"] == "RISK"

    def test_an_alias_chain_carries_provenance(self):
        src = self.BASE + "\nA1 = Base\nA2 = A1\n\nclass Child(A2):\n" \
            + self.CHILD
        assert _scan(src)["rewrite"] == "RISK"

    def test_a_subscript_base_carries_provenance(self):
        src = self.BASE + "\nclass Child(Base[str]):\n" + self.CHILD
        assert _scan(src)["rewrite"] == "RISK"

    def test_an_alias_to_a_clean_class_stays_clean(self):
        """Negative control: an alias must not MINT provenance. The
        base holds a bytes attribute whose .decode is charset decoding,
        and every parse in the child is proven clean."""
        src = '''
import json
from jsonl_utils import loads_clean
from file_lock import atomic_write

class CleanBase:
    def __init__(self):
        self.blob = b"x"

Alias = CleanBase

class Child(Alias):
    def rewrite(self, path):
        out = []
        for line in open(path):
            try:
                out.append(loads_clean(line))
            except Exception:
                continue
        self.blob.decode("utf-8")
        atomic_write(path, "".join(out))
'''
        assert _scan(src)["rewrite"] == "OK"


class TestAliasesInEveryScope:
    """Adversarial r16 (four seats, probed): the r15 alias lattice
    walked MODULE bindings only, and its resolver let a literal class
    name short-circuit a rebinding — so a class-body alias, a
    factory-local alias, and `class Safe: ...; Safe = Dangerous` each
    severed inherited decoder provenance and earned OK from an
    unrelated clean call."""

    def test_a_class_body_alias_carries_provenance(self):
        src = '''
import json
from jsonl_utils import loads_clean
from file_lock import atomic_write

class Outer:
    class Base:
        def __init__(self):
            self.decoder = json.JSONDecoder()
    Alias = Base

class Child(Outer.Alias):
    def rewrite(self, path):
        loads_clean("{}")
''' + _R14_BODY
        assert _scan(src)["rewrite"] == "RISK"

    def test_a_function_local_alias_carries_provenance(self):
        src = '''
import json
from jsonl_utils import loads_clean
from file_lock import atomic_write

def factory():
    class Base:
        def __init__(self):
            self.decoder = json.JSONDecoder()
    Alias = Base
    class Child(Alias):
        def rewrite(self, path):
            loads_clean("{}")
            out = []
            for line in open(path):
                try:
                    out.append(self.decoder.decode(line))
                except Exception:
                    continue
            atomic_write(path, "".join(out))
'''
        assert _scan(src)["factory.rewrite"] == "RISK"

    def test_a_rebound_class_name_unions_both_provenances(self):
        src = '''
import json
from jsonl_utils import loads_clean
from file_lock import atomic_write

class Dangerous:
    def __init__(self):
        self.decoder = json.JSONDecoder()

class Safe:
    pass

Safe = Dangerous

class Child(Safe):
    def rewrite(self, path):
        loads_clean("{}")
''' + _R14_BODY
        assert _scan(src)["rewrite"] == "RISK"
