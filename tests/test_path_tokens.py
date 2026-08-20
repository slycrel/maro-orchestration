"""Root placeholders must be exactly invertible, or the archive lies.

Archives are byte-faithful today and that has already paid off (the flat
lesson ledger was restored 466/466 rows straight out of one). Export-side
substitution keeps that property only while `expand` is an exact inverse of
`substitute`, so most of these tests are round-trip tests.
"""
from __future__ import annotations

import json
import urllib.parse

import pytest

import path_tokens as pt

ROOTS = {
    "workspace_root": "/home/clawd/.maro/workspace",
    "maro_user_dir": "/home/clawd/.maro",
    "repo_root": "/home/clawd/claude/maro-orchestration",
}


@pytest.fixture()
def tmap():
    return pt.build_map(ROOTS)


# ------------------------------------------------------------- round trips

def test_substitute_then_expand_is_the_identity(tmap):
    src = (b"/home/clawd/.maro/workspace/runs/a/build/x.json\n"
           b"/home/clawd/claude/maro-orchestration/src/loop_execute.py\n"
           b"/home/clawd/.maro/config.yml\n")
    sub, n = tmap.substitute(src)
    assert n == 3 and src not in sub
    back, m = tmap.expand(sub)
    assert back == src, "round trip is not byte-identical"
    assert m == 3


def test_nested_roots_take_the_longest_prefix(tmap):
    """`~/.maro/workspace` is inside `~/.maro`; the shorter root must not claim
    the prefix and strand the rest of the path."""
    sub, _ = tmap.substitute(b"/home/clawd/.maro/workspace/runs/a")
    assert sub == b"%%MARO_WORKSPACE%%/runs/a"
    assert b"%%MARO_HOME%%" not in sub


def test_alias_root_normalises_to_the_canonical_root(tmap):
    """The repo was renamed; 6,150 corpus occurrences name the old directory,
    which exists on neither machine. Both must land on the current one."""
    sub, _ = tmap.substitute(
        b"/home/clawd/claude/openclaw-orchestration/src/y.py")
    assert sub == b"%%MARO_REPO%%/src/y.py"
    back, _ = tmap.expand(sub)
    assert back == b"/home/clawd/claude/maro-orchestration/src/y.py"


def test_manifest_records_which_root_was_the_alias(tmap):
    """Translating the old name must not silently equate two names -- the fact
    moves to the manifest rather than being destroyed."""
    rows = tmap.as_manifest()
    repo = [r for r in rows if r["token"] == "%%MARO_REPO%%"]
    assert {r["canonical"] for r in repo} == {True, False}
    canonical = [r for r in repo if r["canonical"]][0]
    assert canonical["root"].endswith("maro-orchestration")


def test_a_role_with_no_recorded_root_is_skipped_not_guessed():
    m = pt.build_map({"workspace_root": "/w"})
    assert m.tokens == ["%%MARO_WORKSPACE%%"]


def test_a_bare_system_root_is_refused():
    """A single-component root is a system directory, not an install root."""
    assert not pt.build_map({"workspace_root": "/"})


# ------------------------------------------------------------- collisions

def test_collision_fails_closed(tmap):
    """A token already present in the content means substitution is not
    invertible; shipping that archive is worse than not shipping one."""
    with pytest.raises(pt.TokenCollision):
        pt.assert_no_collision([b"a %%MARO_WORKSPACE%% b"], tmap)


def test_clean_content_passes_the_collision_check(tmap):
    pt.assert_no_collision([b"/home/clawd/.maro/workspace/x", b"plain"], tmap)


def test_the_chosen_token_survives_json_url_and_format():
    """Probed alternatives both collided: `$MARO_WORKSPACE` occurs 30x in the
    live corpus, and `{{...}}` is str.format's escape syntax (hooks.py uses
    format_map). `%%...%%` survives all three."""
    p = "%%MARO_WORKSPACE%%/runs/a/x.json"
    assert json.loads(json.dumps({"p": p}))["p"] == p
    assert urllib.parse.unquote(urllib.parse.quote(p, safe="/")) == p
    assert "{}".format(p) == p
    assert p.format_map({}) == p          # what hooks.py would do to it


# ------------------------------------------------------------- reader side

def test_resolve_stored_path_expands_against_local_roots(monkeypatch, tmp_path):
    pt.reset_local_map_cache()
    monkeypatch.setattr(pt, "local_map",
                        lambda: pt.build_map({"workspace_root": str(tmp_path)},
                                             aliases=False))
    got = pt.resolve_stored_path("%%MARO_WORKSPACE%%/runs/a/x.json")
    assert got == tmp_path / "runs/a/x.json"


def test_resolve_stored_path_passes_a_plain_path_through(tmp_path):
    """Consumers call it unconditionally, so it must be safe on absolutes."""
    assert pt.resolve_stored_path(str(tmp_path)) == tmp_path


def test_has_token_detects_every_declared_token():
    for token in pt.TOKENS.values():
        assert pt.has_token(f"{token}/x")
    assert not pt.has_token("/plain/path")


def test_local_map_never_emits_an_alias():
    """Expanding on read must produce this install's real root, never a
    historical directory name."""
    m = pt.build_map({"repo_root": "/x/maro-orchestration"}, aliases=False)
    assert [r for r, _ in m.pairs] == ["/x/maro-orchestration"]
