"""Filesystem ground-truth check for fabricated artifact claims (zero-LLM).

The done≠achieved gap: a build step can report status="done" and narrate
"wrote fizzbuzz.py" / "ran the tests, 142 passed" / "verified output 1,2,Fizz…"
without any file actually landing in the workspace. A text-only verdict (the
LLM judge or local validator) can't see the filesystem, so the fabrication
reaches "done" unchallenged.

Now that executor writes are bounded to project_dir (the subprocess cwd is set,
see llm._run_subprocess_safe / step_exec.execute_step), a before/after snapshot
of project_dir is reliable ground truth: if a step *claims* a write but the
workspace didn't change and the claimed file exists nowhere on disk, the step
fabricated its result.

This is the AGENDA build-loop sibling of handle.py's NOW-lane provenance guard
(`_provenance_missing`). Zero LLM, NO code execution, runs in <10ms, and fails
open — any internal error yields a "not fabricated" verdict so the check never
blocks legitimate work on its own bug.

Two layered rules, both grounded in POSITIVE evidence (see check_fabrication):

  1. missing-artifact — a named file write-claim whose target landed nowhere
     (empty project_dir diff AND no claimed path on disk). Misplaced-but-real
     writes (the #1 cwd bug, now fixed) and explicit out-of-workspace writes
     both leave on-disk evidence and are NOT flagged.
  2. inert-output — a concrete stdout claim ("verified output: 1,2,Fizz,4,Buzz")
     against a .py the step produced that is provably inert (purely definitions,
     no __main__/top-level code → prints nothing when run). This is the actual
     organic repro: the file exists (so rule 1 passes) but cannot have produced
     the claimed output. Caught by static AST analysis, not by running the code.

A third "no-path write claim" rule (write-ish words + empty diff, no path named)
was prototyped and REJECTED: it is absence-based, not evidence-based — an empty
diff does not prove fabrication (analysis/planning steps and out-of-workspace
writes legitimately leave it empty), and it false-positived on real completions.
A verifier that hallucinates is its own failure mode; we only flag on positive
evidence.
"""

from __future__ import annotations

import ast
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


# Verb-anchored write-claim regex — mirrors handle.py:_OUTPUT_CLAIM_RE so the
# AGENDA check and the NOW provenance guard extract claims the same way. Matches
# "wrote/saved/created/generated/… to|into|at|as <path>". Requiring the verb is
# what separates a real write-claim from an incidental path mention.
_OUTPUT_CLAIM_RE = re.compile(
    # Mirrors handle.py:_OUTPUT_CLAIM_RE, plus "wrote" (handle's `writ\w*` matches
    # write/writes/writing/written but NOT the irregular past tense "wrote").
    r"\b(?:sav\w*|writ\w*|wrote|creat\w*|output\w*|stor\w*|export\w*|generat\w*|dump\w*)\b"
    r"[^.\n]*?\b(?:to|into|at|as)\s+[`'\"(]?(?P<path>[^\s`'\")]+)",
    re.IGNORECASE,
)
# A claimed token only counts if it looks like a file (has an extension) — this
# drops "saved to memory", "wrote to the database", "stored as a draft", etc.
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")

# --- Layer 2: claimed-output-from-an-inert-artifact -----------------------
# The organic repro: a step writes fizzbuzz.py (so the v1 missing-artifact check
# passes — the file exists), then narrates "Verified output: 1,2,Fizz,4,Buzz,…".
# But the file has no `if __name__ == "__main__"` block and no top-level code, so
# running it prints nothing. The claimed output is fabricated. We catch this by
# static analysis (NO code execution): if the result claims concrete stdout AND
# the produced .py is provably inert (purely definitions), it can't have produced
# that output.
#
# Two-part gate keeps false positives near zero: the result must (a) use a verb
# that asserts runtime stdout — NOT a function "returns" claim, which is true of
# an inert module — and (b) contain concrete output-looking content (a digit, or
# a quoted/backtick literal). A "wrote some helper functions" result has neither.
_STDOUT_CLAIM_RE = re.compile(
    r"\b(?:prints?|printed|printing|stdout|"
    r"output(?:s|ted|ting)?\b(?!\s+(?:file|to|into|path|dir))|"
    r"verified\s+(?:the\s+)?output|confirmed\s+(?:the\s+)?output|"
    r"the\s+output\s+(?:is|was)|produces?\s+(?:the\s+)?output|"
    r"(?:when|after)\s+(?:you\s+)?run|running\s+(?:it|the\b))",
    re.IGNORECASE,
)
# Concrete output-looking content: a digit, a quoted literal, or a backtick span.
_CONCRETE_OUTPUT_RE = re.compile(r"\d|'[^']+'|\"[^\"]+\"|`[^`]+`")

# Directories never worth walking for a workspace diff.
_SKIP_DIRS = frozenset({".git", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"})
# Safety cap so a pathological workspace can't make the diff walk unbounded.
_MAX_FILES = 20000
# mtime jitter tolerance (seconds) — a file is "changed" only if its mtime
# advanced meaningfully past the snapshot.
_MTIME_EPS = 1e-4


@dataclass
class ArtifactVerdict:
    """Result of the filesystem ground-truth check for one step."""
    fabricated: bool
    claims: List[str] = field(default_factory=list)      # write-claims extracted from result
    missing: List[str] = field(default_factory=list)     # claims with no on-disk evidence
    changed_count: int = 0                               # files created/modified in project_dir
    reason: str = ""
    kind: str = ""  # "" | "missing-artifact" | "inert-output" | "execution-contradiction"


def _clean_token(tok: str) -> str:
    return tok.strip().strip("`'\"()").rstrip(".,;:")


def extract_write_claims(text: str) -> List[str]:
    """Extract file paths a result *claims* to have written. Deduplicated.

    Only tokens that look like files (have an extension) are returned.
    """
    out: List[str] = []
    seen: Set[str] = set()
    for m in _OUTPUT_CLAIM_RE.finditer(text or ""):
        tok = _clean_token(m.group("path"))
        if not tok or tok in ("/", "./", "../") or tok.endswith("/"):
            continue
        if not _EXT_RE.search(tok):
            continue
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def files_modified_since(root: Optional[str | os.PathLike], since_iso: str,
                         *, limit: int = 20) -> List[str]:
    """Relpaths under `root` modified at/after `since_iso` (UTC ISO string).

    The resume-side half of the fabrication-guard diff: when a step crashed
    mid-flight, this lists what it managed to touch so the re-executed step
    can complete idempotently instead of blindly redoing. Never raises;
    [] on any parse/walk problem.
    """
    try:
        from datetime import datetime
        ts = datetime.fromisoformat(since_iso).timestamp()
    except (ValueError, TypeError):
        return []
    snap = snapshot_dir(root)
    changed = sorted(rel for rel, mtime in snap.items() if mtime >= ts)
    return changed[:limit]


def snapshot_dir(root: Optional[str | os.PathLike]) -> Dict[str, float]:
    """Map relpath -> mtime for every file under `root`. {} if root is missing.

    Bounded walk: skips VCS/cache dirs and stops after _MAX_FILES. Never raises.
    """
    snap: Dict[str, float] = {}
    if not root:
        return snap
    base = Path(root)
    if not base.is_dir():
        return snap
    count = 0
    try:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                fp = Path(dirpath) / fn
                try:
                    rel = str(fp.relative_to(base))
                    snap[rel] = fp.stat().st_mtime
                except OSError:
                    continue
                count += 1
                if count >= _MAX_FILES:
                    return snap
    except OSError:
        return snap
    return snap


def changed_since(before: Dict[str, float], root: Optional[str | os.PathLike]) -> Set[str]:
    """Relpaths under `root` that are new or whose mtime advanced past `before`."""
    after = snapshot_dir(root)
    changed: Set[str] = set()
    for rel, mtime in after.items():
        prev = before.get(rel)
        if prev is None or (mtime - prev) > _MTIME_EPS:
            changed.add(rel)
    return changed


def _exists_anywhere(claim: str, project_dir: Optional[str | os.PathLike]) -> bool:
    """True if the claimed path resolves to a real file we can credit as evidence.

    An on-disk file is evidence the step did real work — so NOT fabrication. We
    deliberately do NOT consult Path.cwd(): the orchestrator's cwd is the repo
    root, full of unrelated files, and a basename collision there (e.g. a claim
    of "wrote config.py" finding the repo's config.py) would silently mask a
    fabrication. The executor subprocess already runs with cwd=project_dir, so a
    relative write lands in project_dir (checked here) and an absolute write is
    checked by its own path — the cwd of the parent process is never a write
    target worth crediting.
    """
    try:
        p = Path(claim)
        if p.is_absolute():
            return p.exists()
        if project_dir:
            base = Path(project_dir)
            if (base / claim).exists() or (base / p.name).exists():
                return True
    except OSError:
        return False
    return False


def _claims_concrete_stdout(text: str) -> bool:
    """True if the result asserts concrete program stdout (not just a return value).

    Requires BOTH a runtime-stdout verb (prints/output/"when run"/…) AND concrete
    output-looking content (a digit or a quoted/backtick literal). "The function
    returns FizzBuzz for 15" is true of an inert module, so `returns` is excluded.
    """
    if not text:
        return False
    return bool(_STDOUT_CLAIM_RE.search(text) and _CONCRETE_OUTPUT_RE.search(text))


# Top-level AST node types that produce no stdout when a module is run directly.
# Note: class bodies and assignment RHS *can* in principle execute and print, but
# that is rare and the check is additionally gated on a concrete-stdout claim, so
# the residual false-positive surface is negligible.
_INERT_TOPLEVEL = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
    ast.Import, ast.ImportFrom,
    ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Pass,
)


def _python_is_inert(source: str) -> Optional[bool]:
    """True if running this module as `python3 file` provably produces no stdout.

    Inert == the module body is purely definitions/imports/assignments (no
    `if __name__ == "__main__"` block, no top-level calls, loops, or bare
    expressions). Returns None when we can't tell (syntax error) so the caller
    fails open. A docstring (bare constant expression) counts as inert.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    for node in tree.body:
        if isinstance(node, _INERT_TOPLEVEL):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # bare docstring / constant — no output
        return False  # If/For/While/With/Try/bare-call/etc. → may print
    return True


def _python_candidates(
    claims: List[str],
    project_dir: Optional[str | os.PathLike],
    changed: Set[str],
) -> List[Path]:
    """Existing .py files this step plausibly produced (claim paths + fresh files)."""
    out: List[Path] = []
    seen: Set[str] = set()

    def _add(p: Path) -> None:
        try:
            rp = str(p.resolve())
        except OSError:
            rp = str(p)
        if rp not in seen and p.is_file():
            seen.add(rp)
            out.append(p)

    for c in claims:
        if not c.endswith(".py"):
            continue
        cp = Path(c)
        if cp.is_absolute():
            _add(cp)
        elif project_dir:
            _add(Path(project_dir) / c)
    if project_dir:
        base = Path(project_dir)
        for rel in changed:
            if rel.endswith(".py"):
                _add(base / rel)
    return out


def _inert_output_verdict(
    result_text: str,
    project_dir: Optional[str | os.PathLike],
    claims: List[str],
    changed: Set[str],
) -> Optional[ArtifactVerdict]:
    """Layer 2: a concrete stdout claim against a provably-inert .py artifact.

    Returns a fabricated verdict if confirmed, else None (no opinion → caller
    continues). Fails open: any ambiguity (non-Python, parse error, a non-inert
    candidate) yields None.
    """
    if not _claims_concrete_stdout(result_text):
        return None
    cands = _python_candidates(claims, project_dir, changed)
    if not cands:
        return None
    inert_any = False
    for path in cands:
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None  # can't read → can't judge
        verdict = _python_is_inert(src)
        if verdict is None:
            return None  # unparseable → fail open
        if verdict is False:
            return None  # a candidate CAN produce output → claim is plausible
        inert_any = True
    if not inert_any:
        return None
    names = [p.name for p in cands]
    return ArtifactVerdict(
        fabricated=True,
        claims=claims,
        changed_count=len(changed),
        reason=(
            f"step claimed concrete program output but the produced file(s) {names} "
            f"are inert (no __main__/top-level code) and print nothing when run"
        ),
        kind="inert-output",
    )


def check_fabrication(
    result_text: str,
    project_dir: Optional[str | os.PathLike],
    before_snapshot: Dict[str, float],
) -> ArtifactVerdict:
    """Ground-truth check: did a step actually do what it claims?

    Two evidence-based, layered rules (all fail open on any internal error):

      1. missing-artifact — a file write-claim whose target landed nowhere
         (empty workspace diff AND no claimed path on disk).
      2. inert-output — a concrete stdout claim against a .py that is provably
         inert (purely definitions; prints nothing when run). Catches the
         "wrote the file, fabricated the output" shape that rule 1 misses.
    """
    try:
        claims = extract_write_claims(result_text)
        changed = changed_since(before_snapshot, project_dir)

        # Layer 1: missing artifact (a named write-claim with no on-disk evidence).
        if claims:
            missing = [c for c in claims if not _exists_anywhere(c, project_dir)]
            if len(changed) == 0 and len(missing) == len(claims):
                return ArtifactVerdict(
                    fabricated=True,
                    claims=claims,
                    missing=missing,
                    changed_count=len(changed),
                    reason=(
                        f"step claimed to write {claims} but produced no files in "
                        f"the workspace and none exist on disk"
                    ),
                    kind="missing-artifact",
                )

        # Layer 2: claimed output from a provably-inert artifact.
        inert = _inert_output_verdict(result_text, project_dir, claims, changed)
        if inert is not None:
            return inert

        return ArtifactVerdict(
            fabricated=False,
            claims=claims,
            changed_count=len(changed),
            reason="no fabrication signal",
        )
    except Exception:
        return ArtifactVerdict(False, reason="check error (fail-open)")


# ---------------------------------------------------------------------------
# Execution-claim check (done≠achieved, exec variant) — consumes the inner
# agent's REAL tool transcript (resp.tool_events / outcome["tool_events"]).
# ---------------------------------------------------------------------------
#
# The executor's inner `claude -p` actually runs commands via its Bash tool; with
# stream-json the adapter now records those calls and their real exit status.
# That is ground truth for "ran the tests: 142 passed" claims, which the FS-diff
# and AST layers can't reach (no produced .py to inspect).

# Tools whose tool_result reflects a real command execution + exit status.
_EXECUTION_TOOLS = frozenset({"Bash"})

# The result asserts the run SUCCEEDED.
_SUCCESS_CLAIM_RE = re.compile(
    r"\b(?:all\s+)?(?:tests?\s+)?pass(?:ed|es|ing)?\b|"
    r"succe(?:ss|ssful(?:ly)?|eded)\b|\bworks?\b|"
    r"no\s+errors?\b|no\s+failures?\b|\b0\s+fail\w*|"
    r"exit(?:\s+code)?\s+0\b|completed\s+successfully\b|"
    r"built?\s+(?:cleanly|successfully)\b|✓|✅",
    re.IGNORECASE,
)

# The result itself ACKNOWLEDGES a problem — then it is not claiming clean
# success and we must not flag (the agent is being honest about a failed/partial
# run). This is the guard that keeps the check positive-evidence-only.
_FAILURE_ACK_RE = re.compile(
    r"\b(?:fail(?:ed|s|ing|ure)?|error(?:ed|s)?|traceback|exception|"
    r"did\s+not|didn'?t|could\s*n'?t|unable|broke(?:n)?|crash\w*|"
    r"non[- ]zero|exit\s+(?:code\s+)?[1-9])\b",
    re.IGNORECASE,
)


def _tool_failed(te: dict) -> bool:
    """A tool call is a failed execution iff the CLI flagged is_error (the Bash
    tool sets this on non-zero exit). We rely on is_error, NOT a text scan of the
    output — legitimate output like "0 failed" or a test summary mentioning
    "failures: 0" would false-positive a marker scan."""
    return bool(te.get("is_error"))


def _claims_clean_success(text: str) -> bool:
    return bool(_SUCCESS_CLAIM_RE.search(text) and not _FAILURE_ACK_RE.search(text))


def check_execution_claim(result_text: str, tool_events: Optional[List[dict]]) -> ArtifactVerdict:
    """Ground-truth check for execution claims, from the real tool transcript.

    POSITIVE-EVIDENCE only — the single unambiguous contradiction: the step
    claims the run SUCCEEDED, yet every command it actually ran FAILED (non-zero
    exit, is_error), and the result never acknowledges a failure. We hold the
    real exit status, so this is a flat contradiction with ground truth, not an
    absence heuristic.

    Deliberately NOT flagged (too false-positive-prone — mirrors the rejected
    no-path-write layer):
      - "claims execution but ran nothing" — the per-step transcript can't see a
        prior step's legitimate run, so absence here is not proof.
      - partial (some commands succeeded, a later one failed) — telling the test
        command from setup needs intent modeling; a fix-then-succeed flow is
        legitimate and would false-positive.

    Fails open on any internal error.
    """
    try:
        if not tool_events:
            return ArtifactVerdict(False, reason="no tool transcript")
        exec_events = [te for te in tool_events
                       if isinstance(te, dict) and te.get("name") in _EXECUTION_TOOLS]
        if not exec_events:
            return ArtifactVerdict(False, reason="no execution in transcript")
        failed = [te for te in exec_events if _tool_failed(te)]
        succeeded = [te for te in exec_events if not _tool_failed(te)]
        if failed and not succeeded and _claims_clean_success(result_text):
            cmds = [str((te.get("input") or {}).get("command", te.get("name", "?")))[:80]
                    for te in failed]
            return ArtifactVerdict(
                fabricated=True,
                changed_count=len(exec_events),
                reason=(
                    f"step claimed success but all {len(failed)} command(s) it ran "
                    f"failed (non-zero exit): {cmds}"
                ),
                kind="execution-contradiction",
            )
        return ArtifactVerdict(False, reason="execution claim consistent with transcript")
    except Exception:
        return ArtifactVerdict(False, reason="exec check error (fail-open)")


# ---------------------------------------------------------------------------
# Scavenging detector (BACKLOG #1 diagnostic) — out-of-fence file access from
# the REAL tool transcript. Diagnostic only: nothing here changes step status.
# The slycrel-go run-4 contamination (2026-04-17) came from a worker surveying
# a stale clone elsewhere on disk instead of the workspace copy; this makes
# that visible in the captain's log instead of silent.
# ---------------------------------------------------------------------------

_SCAVENGE_READ_TOOLS = frozenset({"Read", "Glob", "Grep", "NotebookRead"})
_SCAVENGE_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
# System prefixes a worker legitimately touches (interpreters, shells, libs) —
# scanning Bash command strings would drown the signal without this filter.
_SCAVENGE_SYSTEM_PREFIXES = (
    "/usr/", "/bin/", "/sbin/", "/etc/", "/lib/", "/lib64/", "/opt/",
    "/proc/", "/sys/", "/dev/", "/run/", "/var/", "/snap/", "/nix/",
)
_SCAVENGE_CAP = 20  # max flagged paths per step (dedup first)
# Lookbehind also excludes ':' and '/' so URL paths ("https://owasp.org/...")
# and colon-prefixed remote/PATH-style entries ("host:/inbox") can't start a
# match — first organic rows (2026-07-03) flagged URL fragments as paths.
_ABS_PATH_RE = re.compile(r"(?<![\w.:/])/(?:[\w.@+-]+/)*[\w.@+-]+")
# cwd-drift tracking (evasion specimen, run 668e46d1 2026-07-04): a worker can
# `cd` out of the fence mid-command and write with RELATIVE paths — invisible
# to both the absolute-path scan and the structured-tool check. We track cd
# targets across the step's Bash commands (worker subprocess cwd persists
# between Bash calls) and resolve relative write targets against the drifted
# cwd. Bare `cd` / `cd ~` goes to $HOME.
_BASH_CD_RE = re.compile(r"(?:^|&&|\|\||;)\s*cd\b(?:[ \t]+([^\s;&|)]+))?", re.MULTILINE)
# Relative targets of shell write operations: output redirection (incl. >>,
# heredoc-feeding `cat > f`) and tee. Target must not start with / (absolute
# paths are handled by the main scan), - (flags), $ (unresolvable) or & (fd dup).
_BASH_REL_WRITE_RE = re.compile(r"(?:(?<![\w>=-])>>?|\btee(?:\s+-a)?)\s+([\w.][\w./+-]*)")


@dataclass
class ScavengeReport:
    """Out-of-fence file access detected in one step's tool transcript."""
    reads: List[dict] = field(default_factory=list)   # {"path", "tool"}
    writes: List[dict] = field(default_factory=list)  # {"path", "tool"}
    truncated: bool = False

    @property
    def flagged(self) -> bool:
        return bool(self.reads or self.writes)


def _in_fence(path: str, fence_roots: List[str]) -> bool:
    try:
        p = os.path.realpath(path)
    except Exception:
        return True  # unresolvable → don't flag
    for root in fence_roots:
        if not root:
            continue
        try:
            r = os.path.realpath(root)
        except Exception:
            continue
        if p == r or p.startswith(r + os.sep):
            return True
    return False


def _is_system_path(path: str) -> bool:
    return path.startswith(_SCAVENGE_SYSTEM_PREFIXES)


def fence_allow_roots() -> List[str]:
    """Always-allowed write roots beyond the project fence (2026-07-04,
    Jeremy: "lean into /tmp... this isn't a sandbox"). Temp scratch is a normal
    worker move, not drift — the fence exists to catch a worker wandering into
    OTHER projects' trees, not to police scratch space. Extend via config
    `validate.write_fence_allow` (list of path prefixes)."""
    roots = ["/tmp", tempfile.gettempdir()]
    try:
        from config import get as _cfg_get
        for r in (_cfg_get("validate.write_fence_allow", []) or []):
            if r:
                roots.append(os.path.expanduser(str(r)))
    except Exception:
        pass
    return roots


# Goal-declared fence widening (2026-07-04, Jeremy: "intent should trump
# correctness"): absolute (or ~-) paths named in the goal TEXT join the fence
# for that run — the fence enforces "the worker stayed where the goal pointed
# it", not "the goal is only allowed to want the workspace". Trust boundary:
# goals are trusted (Jeremy / dispatch navigator authored), workers aren't.
# Same lookbehind as _ABS_PATH_RE so URL fragments can't match.
_GOAL_PATH_RE = re.compile(r"(?<![\w.:/])~?/[\w.@+-]+(?:/[\w.@+-]+)*")
_GOAL_ROOTS_CAP = 8


def goal_declared_roots(goal: str) -> List[str]:
    """Extract absolute paths the goal text explicitly names, as extra fence
    roots. Filters: system prefixes never widen (a goal naming /etc/passwd
    does NOT authorize it), bare top-level dirs ("/data") are too broad, and
    the list is capped. Never raises."""
    roots: List[str] = []
    try:
        seen: Set[str] = set()
        for m in _GOAL_PATH_RE.finditer(goal or ""):
            p = os.path.expanduser(m.group(0).rstrip(".,;:"))
            if not p.startswith("/") or _is_system_path(p):
                continue
            if p.count("/") < 2:  # "/data" — a whole top-level tree is not intent
                continue
            if p not in seen:
                seen.add(p)
                roots.append(p)
            if len(roots) >= _GOAL_ROOTS_CAP:
                break
    except Exception:
        return []
    return roots


def _resolve_cd_target(target: Optional[str], cur_cwd: Optional[str], base: str) -> Optional[str]:
    """Best-effort resolution of a `cd` target against the tracked cwd.

    Returns the new tracked cwd, or None when it can't be resolved (unknown
    state — drift tracking then stays silent rather than guessing).
    """
    if target is None or target in ("~", "$HOME"):
        return os.path.expanduser("~")
    target = target.strip("'\"")
    if not target or target == "-" or target.startswith("$"):
        return None  # cd -, unexpanded vars: unresolvable
    if target.startswith("~"):
        return os.path.normpath(os.path.expanduser(target))
    if os.path.isabs(target):
        return os.path.normpath(target)
    start = cur_cwd or base
    if not start:
        return None
    return os.path.normpath(os.path.join(start, target))


def detect_out_of_fence_access(
    tool_events: Optional[List[dict]],
    fence_roots: List[str],
) -> ScavengeReport:
    """Scan a step's real tool transcript for absolute-path file access outside
    the fence (project dir + canonical workspace).

    Only ABSOLUTE paths are considered: since the cwd fence (2026-06-26 →
    2026-07-03) every relative access lands inside the fence by construction.
    Structured tools (Read/Write/Edit/Glob/Grep) are matched on their path
    input; Bash commands get a best-effort regex scan of the command string
    with system prefixes filtered (heuristic — recorded with tool="Bash" so
    consumers can weigh it accordingly). Never raises; fails to an empty
    report.
    """
    report = ScavengeReport()
    try:
        if not tool_events:
            return report
        seen: set = set()

        def _flag(path: str, tool: str, bucket: List[dict]):
            if len(seen) >= _SCAVENGE_CAP:
                report.truncated = True
                return
            key = (path, tool)
            if key in seen:
                return
            seen.add(key)
            bucket.append({"path": path, "tool": tool})

        # Drift-tracked cwd across the step's Bash calls. None = still the
        # fenced launch cwd (or unresolvable — tracking goes silent, never
        # guesses). The worker subprocess's cwd persists between Bash calls.
        base = fence_roots[0] if fence_roots and fence_roots[0] else ""
        drift_cwd: Optional[str] = None

        def _flag_rel_write(rel: str, at_cwd: Optional[str], tool: str):
            """Flag a relative write target resolved against a drifted cwd."""
            if not at_cwd or _in_fence(at_cwd, fence_roots) or _is_system_path(at_cwd):
                return  # in-fence (or unknown) cwd → relative writes are safe
            resolved = os.path.normpath(os.path.join(at_cwd, rel))
            if not _is_system_path(resolved) and not _in_fence(resolved, fence_roots):
                _flag(resolved, f"{tool}(cwd-drift)", report.writes)

        for te in tool_events:
            if not isinstance(te, dict):
                continue
            name = str(te.get("name", ""))
            inp = te.get("input") or {}
            if not isinstance(inp, dict):
                continue
            if name in _SCAVENGE_READ_TOOLS or name in _SCAVENGE_WRITE_TOOLS:
                raw = str(inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or "")
                if raw and os.path.isabs(raw) and not _is_system_path(raw) \
                        and not _in_fence(raw, fence_roots):
                    bucket = report.writes if name in _SCAVENGE_WRITE_TOOLS else report.reads
                    _flag(raw, name, bucket)
                elif raw and not os.path.isabs(raw) and name in _SCAVENGE_WRITE_TOOLS:
                    # Relative structured write while cwd has drifted out of fence
                    _flag_rel_write(raw, drift_cwd, name)
            elif name == "Bash":
                cmd = str(inp.get("command", ""))
                for m in _ABS_PATH_RE.findall(cmd)[:50]:
                    if _is_system_path(m) or _in_fence(m, fence_roots):
                        continue
                    _flag(m, "Bash", report.reads)
                # cwd-drift walk: interleave cd's and relative write targets in
                # command order so each write resolves against the cwd in effect
                # at its position within this command.
                marks = (
                    [(m.start(), "cd", m.group(1)) for m in _BASH_CD_RE.finditer(cmd)]
                    + [(m.start(), "w", m.group(1)) for m in _BASH_REL_WRITE_RE.finditer(cmd)]
                )
                local_cwd = drift_cwd
                for _pos, kind, val in sorted(marks[:100]):
                    if kind == "cd":
                        local_cwd = _resolve_cd_target(val, local_cwd, base)
                    else:
                        _flag_rel_write(val, local_cwd, "Bash")
                drift_cwd = local_cwd
        return report
    except Exception:
        return ScavengeReport()
