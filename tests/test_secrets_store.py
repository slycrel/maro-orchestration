"""secrets_store — Maro's managed secrets path (docs/SECRETS_DESIGN.md).

Most tests run against a FAKE `sops` / `age-keygen` pair (below) so the
store's contract is pinned on every box and in CI; one round trip runs the
real binaries when they are installed (feature-gated skip, the allowed
kind). The fake keeps sops' observable contract: names cleartext, values
wrapped in `ENC[...]`, `sops_age__list_N__map_recipient=` metadata lines,
exit 128 when the identity file is missing, and `-d --output-type json`,
`--set`, `unset`, `-e --age` verbs.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import secrets_store as ss  # noqa: E402


_FAKE_SOPS = r'''#!/usr/bin/env python3
"""Fake sops for tests: reversible ENC[FAKE:<hex>] values, dotenv only."""
import base64, json, os, sys
argv = sys.argv[1:]
def die(code, msg):
    sys.stderr.write(msg + "\n"); sys.exit(code)
def enc(v): return "ENC[FAKE:" + base64.b16encode(v.encode()).decode() + "]"
def dec(v):
    if v.startswith("ENC[FAKE:") and v.endswith("]"):
        return base64.b16decode(v[len("ENC[FAKE:"):-1]).decode()
    return v
def read(path):
    if not os.path.exists(path): die(100, "no such file")
    names, meta = [], []
    for line in open(path).read().splitlines():
        if not line or line.startswith("#"): continue
        k, _, v = line.partition("=")
        (meta if k.startswith("sops_") else names).append((k, v))
    return names, meta
def write(path, names, meta):
    with open(path, "w") as fh:
        for k, v in names: fh.write(f"{k}={v}\n")
        for k, v in meta: fh.write(f"{k}={v}\n")
def need_key():
    kf = os.environ.get("SOPS_AGE_KEY_FILE", "")
    if not kf or not os.path.exists(kf): die(128, "no key could decrypt the data key")
if "-e" in argv or "--encrypt" in argv:
    recs = [r for i, a in enumerate(argv) if a == "--age" for r in argv[i + 1].split(",")]
    src = argv[-1]
    names = []
    for line in open(src).read().splitlines():
        if not line or line.startswith("#"): continue
        k, _, v = line.partition("=")
        names.append((k, enc(v)))
    meta = []
    for i, r in enumerate(recs):
        meta.append((f"sops_age__list_{i}__map_recipient", r))
    meta.append(("sops_version", "fake"))
    out = "".join(f"{k}={v}\n" for k, v in names + meta)
    sys.stdout.write(out); sys.exit(0)
if "-d" in argv or "--decrypt" in argv:
    need_key()
    path = argv[-1]
    names, _ = read(path)
    if "--extract" in argv:
        want = json.loads(argv[argv.index("--extract") + 1])[0]
        for k, v in names:
            if k == want: sys.stdout.write(dec(v)); sys.exit(0)
        die(1, "no such key")
    sys.stdout.write(json.dumps({k: dec(v) for k, v in names})); sys.exit(0)
if "--set" in argv:
    need_key()
    spec = argv[argv.index("--set") + 1]; path = argv[-1]
    key = json.loads(spec[:spec.index("]") + 1])[0]; val = json.loads(spec[spec.index("]") + 1:].strip())
    names, meta = read(path)
    names = [(k, v) for k, v in names if k != key] + [(key, enc(str(val)))]
    write(path, names, meta); sys.exit(0)
if argv and argv[0] == "unset":
    need_key()
    path = argv[1]; key = json.loads(argv[2])[0]
    names, meta = read(path)
    write(path, [(k, v) for k, v in names if k != key], meta); sys.exit(0)
die(2, "fake sops: unsupported " + " ".join(argv))
'''

_FAKE_KEYGEN = r'''#!/usr/bin/env python3
import sys
argv = sys.argv[1:]
if "-y" in argv:
    for line in open(argv[-1]).read().splitlines():
        if line.startswith("# public key:"): print(line.split(":", 1)[1].strip())
    sys.exit(0)
out = argv[argv.index("-o") + 1]
with open(out, "w") as fh:
    fh.write("# created: now\n# public key: age1fakerecipient000\nAGE-SECRET-KEY-1FAKE\n")
sys.exit(0)
'''


@pytest.fixture
def fake_tools(tmp_path, monkeypatch):
    """A PATH with a fake sops + age-keygen in front, and an isolated
    secrets dir. Returns the secrets dir."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("sops", _FAKE_SOPS), ("age-keygen", _FAKE_KEYGEN)):
        p = bin_dir / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    sdir = tmp_path / "secrets"
    monkeypatch.setenv("MARO_SECRETS_DIR", str(sdir))
    ss.reset_cache()
    yield sdir
    ss.reset_cache()


def _seed(sdir: Path, values: dict, policy: str = "") -> None:
    """A store with values (via the fake), an identity, and a policy."""
    ss.init()
    for k, v in values.items():
        ss.set_value(k, v)
    if policy:
        ss.policy_path().write_text(policy)
    ss.reset_cache()


# ---------------------------------------------------------------------------
# Pure parsers
# ---------------------------------------------------------------------------

class TestParsers:
    def test_parse_names_reads_the_cleartext_side_only(self):
        text = (
            "ALPHA=ENC[AES256_GCM,data:x,iv:y,tag:z,type:str]\n"
            "#ENC[AES256_GCM,data:c,type:comment]\n"
            "\n"
            "BETA=ENC[...]\n"
            "EMPTY=\n"
            "sops_age__list_0__map_recipient=age1abc\n"
            "sops_mac=ENC[...]\n"
            "sops_version=3.13.3\n"
            "ALPHA=dup\n"
            "novalue\n"
        )
        assert ss.parse_names(text) == ["ALPHA", "BETA", "EMPTY"]

    def test_parse_policy_globs_and_comments(self):
        assert ss.parse_policy("# c\nYAHOO_*  # mail\n\nNVIDIA_API_KEY\nYAHOO_*\n") == [
            "YAHOO_*", "NVIDIA_API_KEY"]

    def test_injectable_is_case_sensitive_glob_over_store_order(self):
        names = ["NVIDIA_API_KEY", "YAHOO_USER", "yahoo_lower", "YAHOO_APP_PASSWORD", "OTHER"]
        assert ss.injectable(names, ["YAHOO_*", "OTHER"]) == [
            "YAHOO_USER", "YAHOO_APP_PASSWORD", "OTHER"]
        assert ss.injectable(names, []) == []

    def test_secret_names_are_env_identifiers(self):
        for bad in ("", "1ABC", "A-B", "A B", "A=B", "$X"):
            with pytest.raises(ValueError):
                ss._check_name(bad)
        ss._check_name("YAHOO_APP_PASSWORD")


# ---------------------------------------------------------------------------
# Store lifecycle against the fake
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_no_store_is_quiet(self, fake_tools):
        assert ss.names() == []
        assert ss.load() == {}
        assert ss.container_env() == {}
        assert ss.presence_block([], host=False) == ""
        st = ss.check()
        assert st["store"] is None and st["opens_here"] is None

    def test_init_creates_identity_store_policy_and_marker(self, fake_tools):
        r = ss.init()
        assert r["created_identity"] and r["created_store"] and r["created_policy"]
        assert ss.identity_path().is_file()
        assert stat.S_IMODE(ss.identity_path().stat().st_mode) == 0o600
        assert stat.S_IMODE(ss.store_path().stat().st_mode) == 0o600
        assert ss.names() == ["MARO_SECRETS_STORE"]
        assert ss.recipients() == ["age1fakerecipient000"]
        assert ss.read_meta()["MARO_SECRETS_STORE"]["origin"] == "maro"
        assert "YAHOO_*" in ss.policy_path().read_text()
        # a second init keeps everything
        r2 = ss.init()
        assert not (r2["created_identity"] or r2["created_store"] or r2["created_policy"])

    def test_set_get_unset_with_metadata(self, fake_tools):
        ss.init()
        ss.set_value("ALPHA_KEY", "alpha=1", service="alpha", note="test key")
        assert ss.names() == ["MARO_SECRETS_STORE", "ALPHA_KEY"]
        assert ss.get_value("ALPHA_KEY") == "alpha=1"
        # the encrypted file never carries the value
        assert "alpha=1" not in ss.store_path().read_text()
        m = ss.read_meta()["ALPHA_KEY"]
        assert m["origin"] == "operator" and m["service"] == "alpha" and m["source"] == "cli"
        assert m["created"] == m["updated"]
        # maro-derived overwrite flips origin, keeps created
        ss.set_value("ALPHA_KEY", "alpha=2", origin="maro", source="drop", run="r1")
        m2 = ss.read_meta()["ALPHA_KEY"]
        assert m2["origin"] == "maro" and m2["run"] == "r1" and m2["created"] == m["created"]
        assert ss.get_value("ALPHA_KEY") == "alpha=2"
        assert ss.describe("ALPHA_KEY").startswith("ALPHA_KEY (maro-derived by run r1, ")
        ss.unset_value("ALPHA_KEY")
        assert "ALPHA_KEY" not in ss.names()
        assert "ALPHA_KEY" not in ss.read_meta()

    def test_set_refuses_without_a_store(self, fake_tools):
        with pytest.raises(RuntimeError, match="secrets init"):
            ss.set_value("X", "1")

    def test_load_without_identity_warns_once_and_names_still_work(self, fake_tools, caplog):
        _seed(fake_tools, {"ALPHA_KEY": "a"})
        ss.identity_path().unlink()
        ss.reset_cache()
        with caplog.at_level("WARNING", logger="secrets_store"):
            assert ss.load() == {}
            assert ss.load() == {}
        assert sum("not decrypted" in r.message for r in caplog.records) == 1
        assert "no age identity" in caplog.text
        assert ss.names() == ["MARO_SECRETS_STORE", "ALPHA_KEY"]
        assert ss.check()["opens_here"] is False

    def test_load_is_cached_per_store_mtime(self, fake_tools, monkeypatch):
        _seed(fake_tools, {"ALPHA_KEY": "a"})
        calls = []
        real = ss._run_sops

        def counting(args, **kw):
            calls.append(list(args))
            return real(args, **kw)
        monkeypatch.setattr(ss, "_run_sops", counting)
        ss.load(); ss.load()
        assert len([c for c in calls if c[0] == "-d"]) == 1
        ss.set_value("BETA", "b")     # rewrites the store → cache invalid
        ss.load()
        assert len([c for c in calls if c[0] == "-d"]) == 2

    def test_migrate_folds_plaintext_in_and_keeps_the_file(self, fake_tools, tmp_path):
        plain = tmp_path / "legacy.env"
        plain.write_text('OPENAI_API_KEY="sk-legacy"\n# c\nGROQ_API_KEY=g1\n')
        r = ss.migrate(plain)
        assert r["added"] == ["OPENAI_API_KEY", "GROQ_API_KEY"] and r["kept"] == []
        assert ss.get_value("OPENAI_API_KEY") == "sk-legacy"
        assert plain.is_file(), "the plaintext is the operator's to retire"
        assert ss.read_meta()["OPENAI_API_KEY"]["source"] == "migrate:" + str(plain)
        plain.write_text("OPENAI_API_KEY=sk-new\nNEW=n\n")
        r2 = ss.migrate(plain)
        assert r2["added"] == ["NEW"] and r2["kept"] == ["OPENAI_API_KEY"]
        assert ss.get_value("OPENAI_API_KEY") == "sk-legacy"
        r3 = ss.migrate(plain, overwrite=True)
        assert "OPENAI_API_KEY" in r3["added"]
        assert ss.get_value("OPENAI_API_KEY") == "sk-new"

    def test_add_recipient_rewraps_and_keeps_values(self, fake_tools):
        _seed(fake_tools, {"ALPHA_KEY": "a"})
        recs = ss.add_recipient("age1otherbox")
        assert recs == ["age1fakerecipient000", "age1otherbox"]
        assert ss.get_value("ALPHA_KEY") == "a"
        assert ss.add_recipient("age1otherbox") == recs
        with pytest.raises(ValueError):
            ss.add_recipient("notarecipient")


# ---------------------------------------------------------------------------
# Policy, injection, the presence index
# ---------------------------------------------------------------------------

class TestInjectionAndPresence:
    def test_container_env_follows_the_policy(self, fake_tools):
        _seed(fake_tools, {"YAHOO_USER": "u", "YAHOO_APP_PASSWORD": "p", "NVIDIA_API_KEY": "n"})
        assert ss.container_env() == {}
        ss.policy_path().write_text("YAHOO_*\n")
        assert ss.container_env() == {"YAHOO_USER": "u", "YAHOO_APP_PASSWORD": "p"}

    def test_presence_block_container_forbids_the_absence_claim(self, fake_tools):
        _seed(fake_tools, {"YAHOO_USER": "u", "NVIDIA_API_KEY": "n"}, policy="YAHOO_*\n")
        ss.record_meta("NVIDIA_API_KEY", origin="operator", source="cli", service="nvidia")
        block = ss.presence_block(ss.container_env(), host=False,
                                  drop=Path("/tmp/secrets-derived.env"))
        assert block.startswith("## Secrets")
        assert "Injected into your environment as variables: YAHOO_USER." in block
        assert "Held on the host, NOT injected here: MARO_SECRETS_STORE, NVIDIA_API_KEY" in block
        assert "Never conclude that no credential exists" in block
        assert "NVIDIA_API_KEY (operator, " in block and ", nvidia)" in block
        assert str(ss.policy_path()) in block
        assert "/tmp/secrets-derived.env ($MARO_SECRETS_DROP)" in block
        for value in ("u", "n"):
            assert f"={value}" not in block

    def test_presence_block_host_names_the_read_recipe(self, fake_tools):
        _seed(fake_tools, {"YAHOO_USER": "u"})
        block = ss.presence_block([], host=True)
        assert "Not injected (you are on the host" in block
        assert "sops -d --extract" in block and str(ss.store_path()) in block
        assert "Never conclude" not in block
        assert "MARO_SECRETS_DROP" not in block   # no drop path given


# ---------------------------------------------------------------------------
# The drop file — maro-derived secrets
# ---------------------------------------------------------------------------

class TestDrop:
    def test_ingest_stores_as_maro_derived_and_shreds(self, fake_tools, tmp_path):
        _seed(fake_tools, {})
        drop = tmp_path / "scratch" / ss.DROP_NAME
        drop.parent.mkdir()
        drop.write_text('YAHOO_APP_PASSWORD="abcd efgh"\nSESSION_COOKIE=c=1\n')
        stored = ss.ingest_drop(drop, run="1a2b3c4d", service="yahoo")
        assert stored == ["YAHOO_APP_PASSWORD", "SESSION_COOKIE"]
        assert not drop.exists()
        assert ss.get_value("YAHOO_APP_PASSWORD") == "abcd efgh"
        assert ss.get_value("SESSION_COOKIE") == "c=1"
        m = ss.read_meta()["YAHOO_APP_PASSWORD"]
        assert m == {**m, "origin": "maro", "run": "1a2b3c4d", "source": "drop", "service": "yahoo"}

    def test_ingest_without_a_store_inits_one(self, fake_tools, tmp_path):
        drop = tmp_path / ss.DROP_NAME
        drop.write_text("TOKEN=t\n")
        assert ss.ingest_drop(drop, run="r") == ["TOKEN"]
        assert ss.store_present() and ss.get_value("TOKEN") == "t"

    def test_ingest_keeps_the_file_when_a_name_fails(self, fake_tools, tmp_path, caplog):
        _seed(fake_tools, {})
        drop = tmp_path / ss.DROP_NAME
        drop.write_text("GOOD=1\n1BAD=2\n")
        with caplog.at_level("WARNING", logger="secrets_store"):
            assert ss.ingest_drop(drop, run="r") == ["GOOD"]
        assert drop.exists(), "a partial ingest must not lose the rest"
        assert "1 of 2 names stored" in caplog.text

    def test_ingest_of_missing_or_empty_drop_is_a_noop(self, fake_tools, tmp_path):
        assert ss.ingest_drop(None) == []
        assert ss.ingest_drop(tmp_path / "nope") == []
        empty = tmp_path / ss.DROP_NAME
        empty.write_text("# nothing\n")
        assert ss.ingest_drop(empty) == []
        assert not empty.exists()
        assert not ss.store_present()

    def test_drop_path_needs_a_scratch_dir(self):
        assert ss.drop_path(None) is None
        assert ss.drop_path("/x/scratch") == Path("/x/scratch") / ss.DROP_NAME


# ---------------------------------------------------------------------------
# Provider chain + the executor seams
# ---------------------------------------------------------------------------

class TestChain:
    def test_store_wins_over_legacy_plaintext_which_fills_gaps(self, fake_tools, tmp_path, monkeypatch):
        import config
        monkeypatch.delenv("MARO_ENV_FILE", raising=False)
        legacy = Path(os.environ["MARO_WORKSPACE"]) / "secrets" / ".env"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("OPENAI_API_KEY=legacy\nONLY_LEGACY=l\n")
        _seed(fake_tools, {"OPENAI_API_KEY": "store", "ONLY_STORE": "s"})
        env = config.load_credentials_env()
        assert env["OPENAI_API_KEY"] == "store"
        assert env["ONLY_LEGACY"] == "l" and env["ONLY_STORE"] == "s"
        assert legacy in ss.legacy_env_sources()
        assert str(legacy) in ss.check()["plaintext_residue"]

    def test_a_broken_store_never_takes_the_legacy_path_down(self, fake_tools, monkeypatch):
        import config
        monkeypatch.delenv("MARO_ENV_FILE", raising=False)
        legacy = Path(os.environ["MARO_WORKSPACE"]) / "secrets" / ".env"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("OPENAI_API_KEY=legacy\n")
        monkeypatch.setattr(ss, "load", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        assert config.load_credentials_env()["OPENAI_API_KEY"] == "legacy"


class _FakeProc:
    pid = 99999
    returncode = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class TestExecutorSeams:
    def test_executor_step_injects_policy_names_on_the_host_lane(self, fake_tools, monkeypatch):
        import llm
        _seed(fake_tools, {"YAHOO_USER": "u", "NVIDIA_API_KEY": "n"}, policy="YAHOO_*\n")
        ss.load()   # warm the decrypt cache: Popen is faked below
        captured = {}

        def _fake_popen(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return _FakeProc()
        monkeypatch.setattr("subprocess.Popen", _fake_popen)
        llm._run_subprocess_safe(["true"], timeout=5, executor_step=True)
        # no run dir → nowhere for the hand-off file: the env carries them
        assert captured["env"]["YAHOO_USER"] == "u"
        assert "NVIDIA_API_KEY" not in captured["env"]
        assert ss.DROP_ENV not in captured["env"], "no run dir → no drop path"
        assert ss.FILE_ENV not in captured["env"]
        # a tool-less / non-executor call gets nothing
        llm._run_subprocess_safe(["true"], timeout=5)
        assert "YAHOO_USER" not in captured["env"]

    def test_host_lane_hands_values_over_as_a_file_not_env(self, fake_tools, tmp_path, monkeypatch):
        """With a run scratch the host lane writes a per-step 0600 file and
        tells the child only its path (design §10: a host env is inherited
        by every descendant and /proc-readable; a file is read on purpose).
        The file is shredded when the step ends — on the normal path and on
        the failure path."""
        import llm
        from runs import scoped_run_dir
        _seed(fake_tools, {"YAHOO_USER": "u-secret", "NVIDIA_API_KEY": "n"}, policy="YAHOO_*\n")
        run_dir = tmp_path / "abcd1234-nick"
        run_dir.mkdir()
        hand = run_dir / "scratch" / ss.FILE_NAME
        with scoped_run_dir(run_dir):
            res = llm._run_subprocess_safe(
                ["sh", "-c", 'test -z "$YAHOO_USER" || exit 3; '
                             'test "$(stat -c %a "$MARO_SECRETS_FILE")" = 600 || exit 4; '
                             'grep "^YAHOO_USER=" "$MARO_SECRETS_FILE" | sed "s/=.*/=seen/"; '
                             'grep -c "^NVIDIA" "$MARO_SECRETS_FILE" && exit 5; true'],
                timeout=20, executor_step=True)
        assert res.returncode == 0, res.stdout
        assert "YAHOO_USER=seen" in res.stdout
        assert not hand.exists(), "hand-off file survived the step"
        assert "u-secret" not in res.stdout
        # failure path: a child that dies still leaves no file behind
        with scoped_run_dir(run_dir):
            res = llm._run_subprocess_safe(
                ["sh", "-c", 'test -f "$MARO_SECRETS_FILE" && exit 7'],
                timeout=20, executor_step=True)
        assert res.returncode == 7
        assert not hand.exists()
        # the frame names the file, not variables
        import step_exec
        import container_exec as ce
        monkeypatch.setattr(ce, "container_mode", lambda: "off")
        monkeypatch.setattr(ce, "run_scratch_dir", lambda: str(run_dir / "scratch"))
        host = step_exec.execute_system_for_lane()
        assert "Injected for this step as NAME=value lines in " + str(hand) in host
        assert "($" + ss.FILE_ENV + "; mode 0600, shredded when the step ends): YAHOO_USER." in host
        assert "Injected into your environment as variables" not in host
        assert "u-secret" not in host

    def test_executor_step_announces_the_drop_and_ingests_it(self, fake_tools, tmp_path, monkeypatch):
        import llm
        from runs import scoped_run_dir
        _seed(fake_tools, {})
        run_dir = tmp_path / "abcd1234-nick"
        run_dir.mkdir()
        # a real child: writes a derived credential where the frame said to
        with scoped_run_dir(run_dir):
            res = llm._run_subprocess_safe(
                ["sh", "-c", 'printf "MINTED_TOKEN=tok-1\\n" > "$MARO_SECRETS_DROP"'],
                timeout=20, executor_step=True)
        assert res.returncode == 0
        assert not (run_dir / "scratch" / ss.DROP_NAME).exists()
        assert ss.get_value("MINTED_TOKEN") == "tok-1"
        m = ss.read_meta()["MINTED_TOKEN"]
        assert m["origin"] == "maro" and m["run"] == "abcd1234" and m["source"] == "drop"

    def test_injected_values_are_scrubbed_from_captured_output(self, fake_tools, monkeypatch, tmp_path):
        import llm
        _seed(fake_tools, {"YAHOO_USER": "hunter2secret"}, policy="YAHOO_*\n")
        # no run dir → env carries the value; a child that echoes it is scrubbed
        res = llm._run_subprocess_safe(["sh", "-c", 'echo "user=$YAHOO_USER"'],
                                       timeout=20, executor_step=True)
        assert "hunter2secret" not in res.stdout
        assert "[REDACTED:YAHOO_USER]" in res.stdout
        # with a run dir → the file carries it; a child that cats it is scrubbed too
        from runs import scoped_run_dir
        run_dir = tmp_path / "abcd1234-nick"
        run_dir.mkdir()
        with scoped_run_dir(run_dir):
            res = llm._run_subprocess_safe(["sh", "-c", 'cat "$MARO_SECRETS_FILE"'],
                                           timeout=20, executor_step=True)
        assert "hunter2secret" not in res.stdout
        assert "YAHOO_USER=[REDACTED:YAHOO_USER]" in res.stdout

    def test_container_lane_passes_policy_names_bare(self, fake_tools, monkeypatch, tmp_path):
        """Container branch: policy names ride the bare `-e NAME` passthrough,
        their values only in the docker client's env."""
        import llm
        import container_exec as ce
        _seed(fake_tools, {"YAHOO_USER": "u"}, policy="YAHOO_*\n")
        ss.load()   # warm the decrypt cache: Popen is faked below
        captured = {}

        def _fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env")
            return _FakeProc()
        monkeypatch.setattr("subprocess.Popen", _fake_popen)
        monkeypatch.setattr(ce, "hosted_free_container_env", lambda: {})
        monkeypatch.setattr(ce, "build_mount_map", lambda *a, **k: [])
        monkeypatch.setattr(ce, "introspection_provision", lambda: None)
        monkeypatch.setattr(ce, "attachment_ro_mounts", lambda: [])
        monkeypatch.setattr(ce, "run_scratch_dir", lambda: None)
        monkeypatch.setattr(ce, "kill_container", lambda name: None)
        # not literally `claude`: conftest blocks that basename at this seam
        llm._run_subprocess_safe(["/opt/bin/notclaude", "-p", "x"], timeout=5, cwd=str(tmp_path),
                                 container_name="maro-t", executor_step=True)
        joined = " ".join(captured["cmd"])
        assert "-e YAHOO_USER" in joined and "YAHOO_USER=u" not in joined
        assert captured["env"]["YAHOO_USER"] == "u"

    def test_execute_frame_carries_the_presence_block_per_lane(self, fake_tools, monkeypatch):
        import container_exec as ce
        import step_exec
        _seed(fake_tools, {"YAHOO_USER": "u", "NVIDIA_API_KEY": "n"}, policy="YAHOO_*\n")
        monkeypatch.setattr(ce, "container_mode", lambda: "off")
        host = step_exec.execute_system_for_lane()
        assert host.startswith(step_exec.EXECUTE_SYSTEM)
        assert "## Secrets" in host and "you are on the host" in host
        assert "Injected into your environment as variables: YAHOO_USER." in host, "no run dir → env wording"
        monkeypatch.setattr(ce, "container_mode", lambda: "on")
        monkeypatch.setattr(ce, "container_suppressed", lambda: False)
        monkeypatch.setattr(ce, "image_bakes_verbs", lambda: False)
        monkeypatch.setattr(ce, "hosted_free_container_env", lambda: {"GROQ_API_KEY": "g"})
        monkeypatch.setattr(ce, "run_scratch_dir", lambda: "/host/run/scratch")
        cont = step_exec.execute_system_for_lane()
        assert cont.startswith(step_exec.EXECUTE_SYSTEM_CONTAINER)
        assert "Held on the host, NOT injected here: MARO_SECRETS_STORE, NVIDIA_API_KEY." in cont
        assert "Injected into your environment as variables: YAHOO_USER." in cont
        assert "/tmp/" + ss.DROP_NAME in cont
        assert "=u" not in cont and "=g" not in cont

    def test_execute_frame_unchanged_without_a_store(self, fake_tools, monkeypatch):
        import container_exec as ce
        import step_exec
        monkeypatch.setattr(ce, "container_mode", lambda: "off")
        assert step_exec.execute_system_for_lane() == step_exec.EXECUTE_SYSTEM


# ---------------------------------------------------------------------------
# CLI + doctor
# ---------------------------------------------------------------------------

class TestCLI:
    def test_verbs(self, fake_tools, capsys, monkeypatch):
        import cli
        assert cli.main(["secrets", "check"]) == 1
        assert cli.main(["secrets", "init"]) == 0
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("s3cret\n"))
        assert cli.main(["secrets", "set", "NVIDIA_API_KEY", "--stdin", "--service", "nvidia"]) == 0
        capsys.readouterr()
        assert cli.main(["secrets", "list"]) == 0
        out = capsys.readouterr().out
        assert "NVIDIA_API_KEY (operator, " in out and "s3cret" not in out
        assert cli.main(["secrets", "get", "NVIDIA_API_KEY"]) == 0
        assert capsys.readouterr().out.strip() == "s3cret"
        assert cli.main(["secrets", "check", "--json"]) == 0
        st = json.loads(capsys.readouterr().out)
        assert st["names"] == ["MARO_SECRETS_STORE", "NVIDIA_API_KEY"] and st["opens_here"] is True
        assert cli.main(["secrets", "unset", "NVIDIA_API_KEY"]) == 0
        assert cli.main(["secrets", "get", "NVIDIA_API_KEY"]) == 1
        assert cli.main(["secrets", "recipients"]) == 0
        assert "age1fakerecipient000" in capsys.readouterr().out

    def test_doctor_reports_the_store(self, fake_tools, capsys):
        import doctor
        _seed(fake_tools, {"A": "1"}, policy="A\n")
        doctor.run_doctor()
        out = capsys.readouterr().out
        line = [l for l in out.splitlines() if "Secrets store" in l]
        assert line and line[0].strip().startswith("✓") and "2 names, 1 injectable" in line[0]


# ---------------------------------------------------------------------------
# The real binaries, when present
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("sops") is None or shutil.which("age-keygen") is None,
                    reason="sops/age not installed on this box (brew install sops age)")
def test_real_sops_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_SECRETS_DIR", str(tmp_path / "s"))
    ss.reset_cache()
    r = ss.init()
    assert r["recipient"].startswith("age1")
    ss.set_value("ALPHA_KEY", "alpha=1 with space", service="alpha")
    ss.set_value("BETA", "b")
    assert ss.names() == ["MARO_SECRETS_STORE", "ALPHA_KEY", "BETA"]
    assert ss.load() == {"MARO_SECRETS_STORE": "1", "ALPHA_KEY": "alpha=1 with space", "BETA": "b"}
    text = ss.store_path().read_text()
    assert "alpha=1" not in text and "ENC[AES256_GCM" in text
    ss.unset_value("BETA")
    assert ss.names() == ["MARO_SECRETS_STORE", "ALPHA_KEY"]
    # a second recipient: values survive, both keys listed
    other_key = tmp_path / "other-identity.txt"
    subprocess.run([shutil.which("age-keygen"), "-o", str(other_key)], text=True,
                   capture_output=True, check=True)
    other = [l.split(":", 1)[1].strip() for l in other_key.read_text().splitlines()
             if l.startswith("# public key:")][0]
    assert ss.add_recipient(other) == [r["recipient"], other]
    assert ss.get_value("ALPHA_KEY") == "alpha=1 with space"
    # without the identity the store still lists names and refuses values
    ss.identity_path().unlink()
    ss.reset_cache()
    assert ss.names() == ["MARO_SECRETS_STORE", "ALPHA_KEY"]
    assert ss.load() == {}
