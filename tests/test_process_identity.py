from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

import pytest

import process_identity
from process_identity import owner_is_current, process_start_token


def test_current_process_token_is_stable_and_available():
    first = process_start_token(os.getpid())
    second = process_start_token(os.getpid())
    assert first
    assert first == second


def test_owner_identity_detects_pid_reuse():
    assert not owner_is_current(
        42, "old", alive=lambda pid: True, token_reader=lambda pid: "new"
    )


def test_owner_identity_retains_legacy_or_unreadable_live_owner():
    assert owner_is_current(
        42, None, alive=lambda pid: True, token_reader=lambda pid: "new"
    )
    assert owner_is_current(
        42, "old", alive=lambda pid: True, token_reader=lambda pid: None
    )


def test_owner_identity_rejects_dead_pid_without_reading_token():
    def should_not_read(pid):
        raise AssertionError("dead owner token should not be read")

    assert not owner_is_current(
        42, "old", alive=lambda pid: False, token_reader=should_not_read
    )


def test_ps_fallback_pins_timezone_and_locale(monkeypatch):
    real_read = process_identity.Path.read_text
    seen = {}

    def no_proc(self, *args, **kwargs):
        if str(self).startswith("/proc/"):
            raise FileNotFoundError()
        return real_read(self, *args, **kwargs)

    def fake_run(cmd, **kwargs):
        seen["env"] = kwargs["env"]
        return SimpleNamespace(
            returncode=0, stdout="Mon Jul 13 12:00:00 2026\n"
        )

    monkeypatch.setattr(process_identity.Path, "read_text", no_proc)
    monkeypatch.setattr(process_identity.subprocess, "run", fake_run)
    monkeypatch.setattr(process_identity.sys, "platform", "freebsd")
    monkeypatch.setenv("TZ", "America/Denver")
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")

    assert process_start_token(42)
    assert seen["env"]["TZ"] == "UTC"
    assert seen["env"]["LC_ALL"] == "C"
    assert seen["env"]["LANG"] == "C"


def test_linux_proc_failure_never_crosses_to_ps_token(monkeypatch):
    calls = {"ps": 0}

    def no_proc(self, *args, **kwargs):
        raise OSError("transient proc failure")

    def forbidden_ps(*args, **kwargs):
        calls["ps"] += 1
        raise AssertionError("Linux must not switch token methods")

    monkeypatch.setattr(process_identity.sys, "platform", "linux")
    monkeypatch.setattr(process_identity.Path, "read_text", no_proc)
    monkeypatch.setattr(process_identity.subprocess, "run", forbidden_ps)

    assert process_start_token(42) is None
    assert calls["ps"] == 0


def test_linux_boot_id_failure_is_ambiguous_not_a_new_token(monkeypatch):
    def fake_read(self, *args, **kwargs):
        if str(self).endswith("/stat"):
            tail = ["S", *[str(i) for i in range(4, 22)], "987"]
            return f"42 (worker name) {' '.join(tail)}"
        raise OSError("boot id unavailable")

    monkeypatch.setattr(process_identity.sys, "platform", "linux")
    monkeypatch.setattr(process_identity.Path, "read_text", fake_read)
    assert process_start_token(42) is None


def test_linux_proc_stat_field_22_is_start_token(monkeypatch):
    def fake_read(self, *args, **kwargs):
        if str(self).endswith("/stat"):
            tail = ["S", *[str(i) for i in range(4, 22)], "987"]
            return f"42 (worker ) name) {' '.join(tail)}"
        return "boot-123\n"

    monkeypatch.setattr(process_identity.sys, "platform", "linux")
    monkeypatch.setattr(process_identity.Path, "read_text", fake_read)
    assert process_start_token(42) == process_identity._digest(
        "linux:boot-123:987"
    )


def test_darwin_uses_microsecond_kernel_start_time(monkeypatch):
    monkeypatch.setattr(process_identity.sys, "platform", "darwin")
    monkeypatch.setattr(
        process_identity, "_darwin_start_time", lambda pid: (123, 456)
    )
    monkeypatch.setattr(
        process_identity.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ps fallback used")),
    )
    assert process_start_token(42) == process_identity._digest(
        "darwin:123:456"
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin ABI probe")
def test_real_darwin_libproc_layout_and_timestamp():
    started = process_identity._darwin_start_time(os.getpid())
    assert started is not None
    seconds, microseconds = started
    assert time.time() - 86400 < seconds <= time.time()
    assert 0 <= microseconds < 1_000_000
