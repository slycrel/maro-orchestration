"""Maro's secrets store — one managed path for every way Maro runs.

Decree 2026-09-06 (Jeremy, decision 5870f189): secrets management is the
fix for container blindness, NOT flipping the container off. Maro gets its
own secrets path for every runtime — host Python, the containerized
executor (ENV injection), the Go engine — built on an OSS secret tool,
rather than the operator (or Hermes) hand-carrying credentials into runs.

The tool is **sops + age** (getsops/sops, FiloSottile/age): two single
static binaries with prebuilt releases for Linux and macOS (this box via
brew; mini2 and the M6 mini via GitHub releases — the prebuilt-binaries
rule), no server, no account. Chosen over OpenBao/Infisical (a server to
keep alive on an always-on box that already has too many processes to
watch) and over bare age (whole-file blob: no per-key edit, no names
without the key). Every engine shells out to the `sops` binary; no
library dependency in Python or Go (feedback_deps_and_exceptions).

LAYOUT — machine-level, engine-neutral, next to ~/.maro/config.yml
(`MARO_USER_DIR` moves it, so tests never see the box's store):

    ~/.maro/secrets/maro.sops.env     the store: a dotenv file whose VALUES
                                      are encrypted (AES-GCM, key wrapped
                                      to each age recipient) and whose
                                      NAMES stay cleartext
    ~/.maro/secrets/age-identity.txt  this box's age private key (0600)
    ~/.maro/secrets/inject            policy: one name or glob per line —
                                      which secrets are INJECTED into
                                      executor environments (containers,
                                      Go step subprocesses)

Names-cleartext is the load-bearing property: `names()` reads the store
WITHOUT the key, so every runtime — including a container that must not
hold the key — can render the presence index ("these credentials exist on
the host; these are in your environment") and a worker can never again
conclude "no credential exists anywhere on this box" from inside a view
that was designed not to show it (mailbox-access arc, 2026-09-06).

PROVIDER CHAIN for a credential lookup (config.load_credentials_env):
process env (a caller's explicit choice) > this store > the legacy
plaintext `<workspace>/secrets/.env` > the OpenClaw recovery path. The
legacy file keeps working untouched so nothing breaks the day the store
appears; `maro secrets check` names it as a plaintext residue to retire.

INJECTION POLICY: nothing is injected unless its name matches a line in
`inject`. Inside a worker's environment a credential is visible to a
goal-driven `env` (scrubbed from captured output by llm._scrub_secret_values
so it never persists; the worker's own use of it is the point). The
hosted-free provider keys keep their own consent gate
(container_exec.hosted_free_container_env) — this policy ADDS names, it
never removes that gate's.

Multi-box: one store file, many recipients. `maro secrets recipients add
<age1...>` re-wraps the file key for a new box; the encrypted file can
then travel through the credentials backup or a private repo without ever
being plaintext at rest.

TWO ORIGINS, ONE STORE (Jeremy 2026-09-06: "secrets should be both
user-injected and maro-derived and we might need meta-data on both").
A secret is either entered by the operator (`maro secrets set`, migrate)
or DERIVED by Maro itself — an app password it minted by driving a UI, a
token it was issued, a cookie it captured. Both live in the same store;
what differs is the record around them, kept CLEARTEXT in
`~/.maro/secrets/meta.json` (names are cleartext anyway; who/when/what-for
is not secret and every runtime must be able to read it without the key):

    {"YAHOO_APP_PASSWORD": {"origin": "maro", "created": "...", "updated":
     "...", "run": "<run handle>", "service": "yahoo", "source": "drop",
     "note": "app password minted via account security page"}}

A run hands a derived credential back through the DROP FILE — the frame
names `$MARO_SECRETS_DROP`, a path inside the run's scratch (host-visible
in both lanes: the container's /tmp is the run scratch bind); the worker
writes `NAME=value` lines there instead of into its result, and the host
side ingests the file after the step (`ingest_drop`), stores each value
with origin=maro + the run handle, and shreds the drop. Values never
transit a transcript, a receipt, or the docker argv.

Never logs or prints a value except from the explicit `get` verb.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger("secrets_store")

STORE_NAME = "maro.sops.env"
IDENTITY_NAME = "age-identity.txt"
POLICY_NAME = "inject"
META_NAME = "meta.json"
DROP_NAME = "secrets-derived.env"   # inside a run's scratch dir
DROP_ENV = "MARO_SECRETS_DROP"
ORIGIN_OPERATOR = "operator"
ORIGIN_MARO = "maro"

# sops exit codes we distinguish (sops/cmd/sops/codes/codes.go).
_SOPS_EXIT_NO_KEY = 128       # could not decrypt the data key with any master key
_SOPS_EXIT_MISSING_FILE = 100

_POLICY_TEMPLATE = """\
# Maro secrets injection policy (docs/SECRETS_DESIGN.md).
# One secret NAME or glob per line. Names matching a line are INJECTED as
# environment variables into executor environments: the containerized
# worker (docker -e NAME, value copied from the docker client's env, never
# the argv) and Go engine step subprocesses. Everything else stays on the
# host: the worker is told the name exists and that it was not injected.
# Nothing is injected by default. Examples:
#   YAHOO_*
#   NVIDIA_API_KEY
"""


# ---------------------------------------------------------------------------
# Paths + tool discovery
# ---------------------------------------------------------------------------

def secrets_dir() -> Path:
    """`MARO_SECRETS_DIR` > `<MARO_USER_DIR or ~/.maro>/secrets`."""
    override = os.environ.get("MARO_SECRETS_DIR")
    if override:
        return Path(override).expanduser()
    from config import _maro_dir
    return _maro_dir() / "secrets"


def store_path() -> Path:
    return secrets_dir() / STORE_NAME


def identity_path() -> Path:
    return secrets_dir() / IDENTITY_NAME


def policy_path() -> Path:
    return secrets_dir() / POLICY_NAME


def meta_path() -> Path:
    return secrets_dir() / META_NAME


def sops_bin() -> Optional[str]:
    return shutil.which("sops")


def age_keygen_bin() -> Optional[str]:
    return shutil.which("age-keygen")


def store_present() -> bool:
    return store_path().is_file()


def _sops_env() -> Dict[str, str]:
    """The env a sops call runs under: this box's identity, explicitly —
    never sops' own default key location, so two engines can't disagree
    about which key they used."""
    env = dict(os.environ)
    env["SOPS_AGE_KEY_FILE"] = str(identity_path())
    return env


def _run_sops(args: Sequence[str], *, input_text: Optional[str] = None,
              timeout: float = 30.0) -> subprocess.CompletedProcess:
    """Run the sops binary. Raises RuntimeError when it is missing."""
    bin_ = sops_bin()
    if not bin_:
        raise RuntimeError("sops is not installed (brew install sops age, or a "
                           "release binary from github.com/getsops/sops)")
    return subprocess.run(
        [bin_, *args], input=input_text, text=True, capture_output=True,
        env=_sops_env(), timeout=timeout, check=False)


# ---------------------------------------------------------------------------
# Reading — names without the key, values with it
# ---------------------------------------------------------------------------

def parse_names(text: str) -> List[str]:
    """Secret NAMES from a sops dotenv file's text — the cleartext side of
    the store. Skips comments, blanks and sops' own `sops_*` metadata
    lines. Pure, so both engines can be pinned to the same reading."""
    out: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, _ = line.partition("=")
        name = name.strip()
        if not sep or not name or name.startswith("sops_"):
            continue
        if name not in out:
            out.append(name)
    return out


def names() -> List[str]:
    """Every secret name in the store, WITHOUT decrypting. [] when there is
    no store (or it is unreadable — logged, never raised)."""
    path = store_path()
    try:
        if not path.is_file():
            return []
        return parse_names(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        log.warning("secrets store unreadable (%s): %s", path, exc)
        return []


_cache: Dict[str, object] = {}


def load(*, use_cache: bool = True) -> Dict[str, str]:
    """Decrypt the store into {NAME: value}. {} when there is no store, no
    sops, or the key cannot open it — each logged ONCE per process at
    WARNING (the presence index still works from names()). Cached per
    store mtime so a run's many lookups cost one decrypt."""
    path = store_path()
    if not path.is_file():
        return {}
    try:
        stamp = (path.stat().st_mtime_ns, str(identity_path()))
    except OSError:
        stamp = (0, str(identity_path()))
    if use_cache and _cache.get("stamp") == stamp:
        return dict(_cache.get("values") or {})  # type: ignore[arg-type]
    values: Dict[str, str] = {}
    try:
        proc = _run_sops(["-d", "--output-type", "json", str(path)])
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        _warn_once("sops-unavailable", "secrets store present at %s but cannot be "
                   "decrypted here: %s", path, exc)
        _cache.update(stamp=stamp, values={})
        return {}
    if proc.returncode != 0:
        why = ("no age identity can open it (SOPS_AGE_KEY_FILE=%s)" % identity_path()
               if proc.returncode == _SOPS_EXIT_NO_KEY
               else "sops exit %d: %s" % (proc.returncode, (proc.stderr or "").strip()[-200:]))
        _warn_once("sops-decrypt", "secrets store %s not decrypted: %s", path, why)
        _cache.update(stamp=stamp, values={})
        return {}
    try:
        raw = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        _warn_once("sops-json", "secrets store %s decrypted to non-JSON: %s", path, exc)
        _cache.update(stamp=stamp, values={})
        return {}
    for k, v in (raw.items() if isinstance(raw, dict) else []):
        if isinstance(k, str) and not k.startswith("sops"):
            values[k] = "" if v is None else str(v)
    _cache.update(stamp=stamp, values=dict(values))
    return values


_warned: set = set()


def _warn_once(key: str, msg: str, *args: object) -> None:
    if key in _warned:
        return
    _warned.add(key)
    log.warning(msg, *args)


def reset_cache() -> None:
    _cache.clear()
    _warned.clear()


# ---------------------------------------------------------------------------
# Injection policy
# ---------------------------------------------------------------------------

def parse_policy(text: str) -> List[str]:
    """Globs from the policy file's text: one per line, `#` comments."""
    out: List[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and line not in out:
            out.append(line)
    return out


def policy() -> List[str]:
    path = policy_path()
    try:
        if not path.is_file():
            return []
        return parse_policy(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        log.warning("secrets policy unreadable (%s): %s", path, exc)
        return []


def injectable(candidates: Iterable[str], globs: Optional[Sequence[str]] = None) -> List[str]:
    """The names (store order kept) the policy lets into an executor env.
    Matching is case-sensitive fnmatch: `YAHOO_*` covers YAHOO_USER and
    YAHOO_APP_PASSWORD; a bare name matches itself."""
    globs = list(policy() if globs is None else globs)
    if not globs:
        return []
    return [n for n in candidates if any(fnmatch.fnmatchcase(n, g) for g in globs)]


def container_env() -> Dict[str, str]:
    """{NAME: value} for the policy-injectable secrets — what rides into an
    executor container (bare `-e NAME` + docker-client env) or a Go step
    subprocess. {} when nothing is allowed or nothing decrypts."""
    allowed = injectable(names())
    if not allowed:
        return {}
    values = load()
    return {n: values[n] for n in allowed if n in values}


# ---------------------------------------------------------------------------
# Metadata — cleartext, per name: origin (operator | maro), when, which run,
# which service. Read by every runtime; written only by the verbs below.
# ---------------------------------------------------------------------------

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_meta() -> Dict[str, Dict[str, str]]:
    """{NAME: {origin, created, updated, run?, service?, source?, note?}}.
    A missing or unparseable file is {} — logged, never raised: the store
    keeps serving values even if its record is torn."""
    path = meta_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("secrets metadata unreadable (%s): %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        log.warning("secrets metadata %s is not an object; ignored", path)
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def _write_meta(meta: Dict[str, Dict[str, str]]) -> None:
    _ensure_dir()
    tmp = meta_path().with_suffix(".json.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, meta_path())


def record_meta(name: str, *, origin: str, source: str, run: Optional[str] = None,
                service: Optional[str] = None, note: Optional[str] = None) -> Dict[str, str]:
    """Upsert one name's record. `created` survives an update; `origin`
    flips to whoever last set the value (an operator overriding a
    maro-derived value is an operator value now — the history is in the
    decision journal / run records, not here)."""
    if origin not in (ORIGIN_OPERATOR, ORIGIN_MARO):
        raise ValueError("origin must be operator or maro, got %r" % origin)
    meta = read_meta()
    now = _now()
    rec = dict(meta.get(name) or {})
    rec.setdefault("created", now)
    rec.update({"origin": origin, "updated": now, "source": source})
    for k, v in (("run", run), ("service", service), ("note", note)):
        if v:
            rec[k] = str(v)
    meta[name] = rec
    _write_meta(meta)
    return rec


def forget_meta(name: str) -> None:
    meta = read_meta()
    if name in meta:
        del meta[name]
        _write_meta(meta)


def describe(name: str, meta: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    """`NAME (operator, 2026-09-06, yahoo)` / `NAME (maro-derived by run
    1a2b3c4d, 2026-09-06)` — the compact form the presence index uses."""
    rec = (meta if meta is not None else read_meta()).get(name) or {}
    if not rec:
        return name
    bits: List[str] = []
    origin = rec.get("origin")
    if origin == ORIGIN_MARO:
        bits.append("maro-derived" + (" by run %s" % rec["run"] if rec.get("run") else ""))
    elif origin:
        bits.append(str(origin))
    stamp = (rec.get("updated") or rec.get("created") or "")[:10]
    if stamp:
        bits.append(stamp)
    if rec.get("service"):
        bits.append(str(rec["service"]))
    return "%s (%s)" % (name, ", ".join(bits)) if bits else name


# ---------------------------------------------------------------------------
# The drop file — how a run hands a derived credential back
# ---------------------------------------------------------------------------

def drop_path(scratch_dir: Optional[str]) -> Optional[Path]:
    """The drop file for a run scratch dir (None when the run has none)."""
    if not scratch_dir:
        return None
    return Path(scratch_dir) / DROP_NAME


def drop_instructions(path: Path) -> str:
    return ("If you OBTAIN a new credential while working (an app password you "
            "minted, a token you were issued, a session cookie), do not put it in "
            "your result: append `NAME=value` lines to " + str(path) + " "
            "($" + DROP_ENV + "). Maro stores it in the secrets store, records that "
            "this run derived it, and shreds the file. Say in your result WHICH "
            "name you dropped and for what service — never the value.")


def ingest_drop(path: Optional[Path], *, run: Optional[str] = None,
                service: Optional[str] = None) -> List[str]:
    """Store every NAME=value in the drop file as maro-derived, then remove
    the file (ephemeral staging: its only purpose is the hand-off, and a
    plaintext credential must not outlive the step — see
    tests/test_no_silent_deletion.py). Returns the names stored. A store
    that cannot be opened here leaves the drop in place and warns, so the
    value is not lost."""
    if path is None or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("secrets drop unreadable (%s): %s", path, exc)
        return []
    from config import _parse_dotenv_text
    pairs = _parse_dotenv_text(text)
    if not pairs:
        _shred(path)
        return []
    if not store_present():
        try:
            init()
        except Exception as exc:
            log.warning("secrets drop at %s kept: no store and init failed: %s", path, exc)
            return []
    stored: List[str] = []
    for name, value in pairs.items():
        try:
            _check_name(name)
            set_value(name, value, origin=ORIGIN_MARO, source="drop", run=run, service=service)
            stored.append(name)
        except (ValueError, RuntimeError) as exc:
            log.warning("secrets drop: %s not stored: %s", name, exc)
    if len(stored) == len(pairs):
        _shred(path)
    else:
        log.warning("secrets drop at %s kept: %d of %d names stored",
                    path, len(stored), len(pairs))
    if stored:
        log.info("secrets: run %s derived %s", run or "?", ", ".join(stored))
    return stored


def _shred(path: Path) -> None:
    try:
        size = path.stat().st_size
        with open(path, "r+b") as fh:
            fh.write(b"\0" * size)
            fh.flush()
            os.fsync(fh.fileno())
        path.unlink()
    except OSError as exc:
        log.warning("secrets drop %s could not be shredded: %s", path, exc)


# ---------------------------------------------------------------------------
# The presence index — what a worker is told
# ---------------------------------------------------------------------------

def presence_block(injected: Iterable[str] = (), *, host: bool,
                   drop: Optional[Path] = None) -> str:
    """The `## Secrets` paragraph for an execute frame. Identical wording
    in the Go engine (internal/secrets) — the two engines must tell a
    worker the same thing about the same store. Empty when no store.

    `host=True`: the worker runs on the host as the operator's user, so
    the withheld names are one command away and the block says so.
    `host=False` (container): withheld names exist on the host and are
    NOT reachable — the block forbids the "no credential exists" reading.
    """
    known = names()
    if not known:
        return ""
    meta = read_meta()
    inj = [n for n in known if n in set(injected)]
    held = [n for n in known if n not in set(injected)]
    lines = ["## Secrets",
             "Credentials for this machine are managed by Maro's secrets store "
             "(sops + age; names are readable, values are encrypted). "
             "Names in the store: " + ", ".join(describe(n, meta) for n in known) + "."]
    if inj:
        lines.append("Injected into your environment as variables: " + ", ".join(inj) + ".")
    if held:
        if host:
            lines.append(
                "Not injected (you are on the host, same user as the operator): "
                + ", ".join(held) + ". Read one with `sops -d --extract '[\"NAME\"]' "
                + str(store_path()) + "` (SOPS_AGE_KEY_FILE=" + str(identity_path())
                + "); never print or persist a value.")
        else:
            lines.append(
                "Held on the host, NOT injected here: " + ", ".join(held)
                + ". Never conclude that no credential exists for these — report "
                "\"exists in the Maro secrets store but is not injected into this "
                "environment\" and name the variable; the operator enables it by "
                "adding the name to " + str(policy_path()) + ".")
    if drop is not None:
        lines.append(drop_instructions(drop))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Management verbs (maro secrets ...)
# ---------------------------------------------------------------------------

def recipient() -> Optional[str]:
    """This box's age public key, from the identity file's `# public key:`
    line (age-keygen writes it) — or derived via `age-keygen -y`."""
    path = identity_path()
    if not path.is_file():
        return None
    try:
        for line in path.read_text().splitlines():
            if line.startswith("# public key:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    bin_ = age_keygen_bin()
    if not bin_:
        return None
    proc = subprocess.run([bin_, "-y", str(path)], text=True, capture_output=True, check=False)
    return proc.stdout.strip() or None


def _ensure_dir() -> Path:
    d = secrets_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def init(*, force: bool = False) -> Dict[str, object]:
    """Create the secrets dir, an age identity, an empty store and the
    policy template. Refuses to overwrite an identity (it is the only way
    to open the store) unless force=True. Returns a status dict."""
    if not sops_bin() or not age_keygen_bin():
        raise RuntimeError("sops and age are required: brew install sops age "
                           "(or release binaries from github.com/getsops/sops and "
                           "github.com/FiloSottile/age)")
    d = _ensure_dir()
    ident = identity_path()
    created_identity = False
    if ident.exists() and not force:
        pass
    else:
        proc = subprocess.run([age_keygen_bin() or "age-keygen", "-o", str(ident)],
                              text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError("age-keygen failed: " + (proc.stderr or "").strip()[-200:])
        os.chmod(ident, 0o600)
        created_identity = True
    rec = recipient()
    if not rec:
        raise RuntimeError("no age recipient in %s" % ident)
    created_store = False
    if not store_path().exists():
        _write_encrypted({"MARO_SECRETS_STORE": "1"}, [rec])
        record_meta("MARO_SECRETS_STORE", origin=ORIGIN_MARO, source="init",
                    note="store marker written by maro secrets init")
        created_store = True
    created_policy = False
    if not policy_path().exists():
        policy_path().write_text(_POLICY_TEMPLATE)
        created_policy = True
    reset_cache()
    return {"dir": str(d), "identity": str(ident), "recipient": rec,
            "created_identity": created_identity, "created_store": created_store,
            "created_policy": created_policy}


def _write_encrypted(values: Dict[str, str], recipients: Sequence[str]) -> None:
    """Encrypt `values` to `recipients` as a fresh store file. The plaintext
    exists only as a 0600 temp file inside the secrets dir for the length
    of one sops call, then is unlinked (ephemeral staging — see
    tests/test_no_silent_deletion.py)."""
    d = _ensure_dir()
    fd, tmp = tempfile.mkstemp(prefix=".stage-", suffix=".env", dir=str(d))
    try:
        with os.fdopen(fd, "w") as fh:
            for k, v in values.items():
                fh.write(f"{k}={v}\n")
        os.chmod(tmp, 0o600)
        # one --age flag, comma-joined: sops keeps only the LAST --age given
        args = ["-e", "--input-type", "dotenv", "--output-type", "dotenv",
                "--age", ",".join(recipients), tmp]
        proc = _run_sops(args)
        if proc.returncode != 0:
            raise RuntimeError("sops encrypt failed: " + (proc.stderr or "").strip()[-200:])
        out = store_path()
        out_tmp = out.with_suffix(out.suffix + ".tmp")
        out_tmp.write_text(proc.stdout)
        os.chmod(out_tmp, 0o600)
        os.replace(out_tmp, out)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    reset_cache()


def set_value(name: str, value: str, *, origin: str = ORIGIN_OPERATOR,
              source: str = "cli", run: Optional[str] = None,
              service: Optional[str] = None, note: Optional[str] = None) -> None:
    """Add or replace one secret in place (`sops --set`) and record who
    set it (metadata is written AFTER the value lands, so a torn write
    leaves a value without a record rather than a record without a value)."""
    _check_name(name)
    if not store_present():
        raise RuntimeError("no store at %s — run `maro secrets init` first" % store_path())
    proc = _run_sops(["--set", '["%s"] %s' % (name, json.dumps(value)), str(store_path())])
    if proc.returncode != 0:
        raise RuntimeError("sops --set failed: " + (proc.stderr or "").strip()[-200:])
    reset_cache()
    record_meta(name, origin=origin, source=source, run=run, service=service, note=note)


def unset_value(name: str) -> None:
    _check_name(name)
    proc = _run_sops(["unset", str(store_path()), '["%s"]' % name])
    if proc.returncode != 0:
        raise RuntimeError("sops unset failed: " + (proc.stderr or "").strip()[-200:])
    reset_cache()
    forget_meta(name)


def _check_name(name: str) -> None:
    if not name or not all(c.isalnum() or c == "_" for c in name) or name[0].isdigit():
        raise ValueError("secret names are ENV-style identifiers: %r" % name)


def get_value(name: str) -> Optional[str]:
    return load().get(name)


def edit_argv() -> List[str]:
    """The interactive edit command (`sops <store>`) — argv + env for the
    caller to exec; the store runs the operator's $EDITOR."""
    bin_ = sops_bin()
    if not bin_:
        raise RuntimeError("sops is not installed")
    return [bin_, str(store_path())]


def legacy_env_sources() -> List[Path]:
    """Plaintext credential files the chain still falls back to, in order."""
    from config import credentials_env_file, secrets_dir as _ws_secrets
    out: List[Path] = []
    for p in (credentials_env_file(), _ws_secrets() / ".env"):
        if p.is_file() and p not in out:
            out.append(p)
    return out


def migrate(source: Optional[Path] = None, *, overwrite: bool = False) -> Dict[str, object]:
    """Fold a plaintext dotenv into the store: every NAME in `source` that
    the store lacks is added; existing names are kept unless overwrite=True.
    The plaintext file is NOT deleted (data-retention decree: the operator
    retires it by hand once `maro secrets check` shows the store serving).
    Returns counts + the names added (never values)."""
    from config import _parse_dotenv_text
    src = source
    if src is None:
        legacy = legacy_env_sources()
        if not legacy:
            raise RuntimeError("no plaintext credentials file to migrate")
        src = legacy[0]
    if not store_present():
        init()
    pairs = _parse_dotenv_text(Path(src).read_text())
    current = load(use_cache=False)
    added: List[str] = []
    skipped: List[str] = []
    for k, v in pairs.items():
        if k in current and not overwrite:
            skipped.append(k)
            continue
        set_value(k, v, origin=ORIGIN_OPERATOR, source="migrate:" + str(src))
        added.append(k)
    return {"source": str(src), "added": added, "kept": skipped,
            "store": str(store_path())}


def recipients() -> List[str]:
    """Age recipients the store is wrapped to (cleartext sops metadata)."""
    path = store_path()
    if not path.is_file():
        return []
    out: List[str] = []
    try:
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith("sops_age__list_") and "__map_recipient=" in line:
                out.append(line.split("=", 1)[1].strip())
    except OSError:
        pass
    return out


def add_recipient(rec: str) -> List[str]:
    """Re-wrap the store's data key for one more age recipient (another
    box). Needs this box's identity to open the store."""
    if not rec.startswith("age1"):
        raise ValueError("an age recipient starts with age1")
    values = load(use_cache=False)
    if not values and names():
        raise RuntimeError("cannot open the store here; add recipients from a box that holds a key")
    recs = recipients()
    if rec in recs:
        return recs
    _write_encrypted(values, recs + [rec])
    return recipients()


def check() -> Dict[str, object]:
    """One status dict for `maro secrets check` / doctor: tool presence,
    store/identity/policy presence, name count, whether the store opens
    here, and plaintext residues still in the chain."""
    st = store_path()
    known = names()
    opened: Optional[bool] = None
    if st.is_file():
        opened = bool(sops_bin()) and identity_path().is_file() and (
            bool(load(use_cache=False)) or not known)
    return {
        "dir": str(secrets_dir()),
        "sops": sops_bin(),
        "age_keygen": age_keygen_bin(),
        "store": str(st) if st.is_file() else None,
        "identity": str(identity_path()) if identity_path().is_file() else None,
        "recipient": recipient(),
        "recipients": recipients(),
        "names": known,
        "meta": read_meta(),
        "opens_here": opened,
        "policy": policy(),
        "injectable": injectable(known),
        "plaintext_residue": [str(p) for p in legacy_env_sources()],
    }


def render_check(status: Dict[str, object]) -> str:
    def yn(v: object) -> str:
        return "yes" if v else "no"
    lines = [
        f"secrets dir:   {status['dir']}",
        f"sops:          {status['sops'] or 'MISSING (brew install sops age)'}",
        f"age-keygen:    {status['age_keygen'] or 'MISSING'}",
        f"identity:      {status['identity'] or 'none (maro secrets init)'}",
        f"recipient:     {status['recipient'] or '-'}",
        f"store:         {status['store'] or 'none (maro secrets init)'}",
        f"recipients:    {len(status['recipients'])}",
        f"names ({len(status['names'])}): "
        + (", ".join(describe(n, status["meta"]) for n in status["names"]) or "-"),
        f"opens here:    {'-' if status['opens_here'] is None else yn(status['opens_here'])}",
        f"inject policy: {', '.join(status['policy']) or '(nothing injected)'}",
        f"injectable:    {', '.join(status['injectable']) or '-'}",
    ]
    res = status["plaintext_residue"]
    if res:
        lines.append("plaintext residue still in the lookup chain (retire once the store "
                     "serves): " + ", ".join(res))
    return "\n".join(lines)
