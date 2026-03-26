# Security Model

How poe-orchestration thinks about trust, isolation, and keeping secrets secret.

---

## Trust Boundaries

Two boundaries matter:

```
1. Local filesystem (projects/, memory/)
   → Source of truth. Trusted. Plain text. Git-reviewable.
   → No secrets stored here by default (enforce via .gitignore).

2. Queue / external execution (sandboxed skills, tool calls, API requests)
   → Untrusted by default. Every skill runs sandboxed.
   → Network access blocked unless explicitly opted in per-skill.
```

---

## Sandbox Hardening (Phase 18)

Every skill execution goes through `run_skill_sandboxed()` with:

- **Static analysis** before execution: blocklist of dangerous patterns (`import os`, `subprocess`, `eval`, `exec`, `socket.connect`, `requests`, `pickle.loads`, `ctypes`, etc.)
- **Resource limits** via `preexec_fn`: `RLIMIT_CPU`, `RLIMIT_FSIZE`, `RLIMIT_NOFILE` set in child process. Wall-clock timeout enforced via `subprocess.run(timeout=...)`.
- **Network isolation** (soft): `socket.socket.connect` monkey-patched to raise `ConnectionRefusedError` — no root required.
- **Audit log**: every execution logged to `memory/sandbox-audit.jsonl` with outcome, timing, and static analysis result. Failures never block execution.

`RLIMIT_AS` intentionally omitted — breaks Python mmap on Linux with memory overcommit.

---

## Human Gates

Actions that require explicit Jeremy approval regardless of autonomy tier:

| Action | Why gated |
|--------|-----------|
| Money / real trades | Irreversible financial impact |
| Posting publicly as Jeremy | Represents a real person |
| Writing to AGENTS.md / SOUL.md | Identity change; wrong = every session wrong |
| Deleting non-git-tracked data | No rollback |
| Exposing private data externally | Privacy + credentials |
| Canon promotion (lesson → identity) | High-value, high-risk identity change |

Everything else: act first, forgiveness over permission.

---

## Secrets Handling

- Credentials live in `<workspace>/secrets/.env` — outside git, never in `projects/`
- Legacy path: `~/.openclaw/workspace/secrets/recovered/runtime-credentials/.env` (fallback only)
- `POE_ENV_FILE` env var for explicit override
- `config.load_credentials_env()` is the single entry point — never hand-load `.env` files elsewhere
- `OPENCLAW_CFG` env var protects gateway credentials from hardcoding
- **Rule**: if a file path is being `cat`'d, logged, or written to an artifact, check it doesn't contain a secret first

---

## Operator Checklist

- Keep `secrets/.env` in `.gitignore` (already done)
- Review `DECISIONS.md` / `PROVENANCE.md` before sharing a project directory
- `memory/sandbox-audit.jsonl` may contain skill output snippets — don't share without review
- Run `pytest` before deploying any skill mutation
- `deploy/systemd/*.service` and `deploy/launchd/*.plist` embed `POE_WORKSPACE` path — regenerate via `poe-bootstrap services` if workspace moves

---

## Future Work

- **`poe-security audit` CLI**: scan `projects/` for accidental secrets (API key patterns, tokens in DECISIONS.md), flag non-sandboxed tool calls, check `.gitignore` coverage. 95% of the data is already in the audit log and project files — this is an inspector pass over security signals specifically.
- **Capability declarations**: skills declare which capabilities they need (`network: true`, `filesystem: write`) in their markdown. Sandbox enforces the declared subset rather than the current all-or-nothing block.
- **Secrets rotation detection**: if a credential pattern appears in `memory/outcomes.jsonl` or daily logs, flag it.
