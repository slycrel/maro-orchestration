// Package heartbeat ports the deterministic half of Python heartbeat.py:
// the recovery-action data model, the tier-1 scripted recoveries, the
// tier-2 diagnosis cooldown, the tier-3 escalation message, the heartbeat
// log row, and the two cadence resolvers.
//
// WHAT IS NOT HERE, NAMED (do not mistake absence for coverage):
//
//   - `_tier2_llm_diagnosis` — one LLM call per stuck project. Lands when
//     the diagnosis prompt is ported; the COOLDOWN that guards it IS here,
//     because the cooldown is the part with the overnight-runaway history
//     and it is pure arithmetic over a clock.
//   - `run_heartbeat` and `heartbeat_loop` — the orchestration, the five
//     background threads, the daemon pidfile and the SlowUpdateScheduler.
//   - `stranded_state_sweep`, `_backfill_stranded_run_cards`,
//     `_find_resumable_runs` — crash recovery. Its own slice; it reads run
//     cards, leases and checkpoints, which is three stores.
//   - `_is_interactive_session_active` — scans the process table for
//     `claude --continue` and compares cwds.
//   - `post_heartbeat_event` — a process-local threading.Event plus a typed
//     event post. The Go shape is a channel, and inventing one before there
//     is a loop to wake would be a guess.
package heartbeat

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Cadence defaults, verbatim from heartbeat.py.
const (
	DefaultBacklogEvery       = 5
	DefaultBacklogBatchSize   = 3
	DefaultShadowEvery        = 0
	DefaultTaskStoreBatchSize = 6
)

// DiagnosisCooldown is `_DIAGNOSIS_COOLDOWN_SECS`. At a 60s tick this is at
// most two diagnoses per hour per project; without it six stuck projects
// cost 360 LLM calls an hour, which is the overnight runaway it was added
// for.
const DiagnosisCooldown = 1800 * time.Second

// ---------------------------------------------------------------------------
// The data model
// ---------------------------------------------------------------------------

// RecoveryAction is heartbeat.RecoveryAction.
//
// Tier is an int and not a named type: the dataclass takes whatever it is
// given, `to_dict` writes it through, and `summary` renders it with `{}` —
// a tier of "X" really does print `[tierX]`, measured. A Go enum would
// refuse a tier 4 that Python happily records into a ledger both runtimes
// read.
type RecoveryAction struct {
	Tier    int
	Target  string
	Action  string
	Outcome string // "fixed" | "suggested" | "escalated" | "skipped"
	Detail  string
}

// Report is heartbeat.HeartbeatReport.
//
// Checks is a pyval.Obj rather than a map[string]string for two reasons,
// and both are load-bearing:
//
//   - Its ORDER is observable twice — tier 1 emits one action per failing
//     check in CHECK order, and the escalation's "Failed checks:" line joins
//     the failing names in the same order. SystemHealth builds that dict in
//     a fixed sequence, so a Go map would randomise a rendered string.
//   - Its VALUES are not guaranteed strings. Python annotates
//     `Dict[str, str]` and does not enforce it; a non-string detail reaches
//     `detail.startswith(...)` and raises AttributeError from inside both
//     tier 1 and tier 3. A map[string]string in Go would make that
//     unrepresentable and silently drop a real failure mode.
type Report struct {
	RunID           string
	CheckedAt       string
	HealthStatus    string // "healthy" | "degraded" | "critical"
	Checks          pyval.Obj
	StuckProjects   []string
	RecoveryActions []RecoveryAction
	TelegramSent    bool
	ElapsedMS       int
	QualitySummary  string
}

// Summary is HeartbeatReport.summary().
//
// Two renderings here are not what a port reaches for, both measured:
//
//   - `{self.stuck_projects or 'none'}` prints the literal string "none"
//     when the list is EMPTY, and Python's list REPR otherwise — so ONE
//     stuck project renders `['alpha']`, brackets and quotes included, and
//     a name containing an apostrophe flips repr to double quotes.
//   - `{self.telegram_sent}` is `str(bool)`, so "True"/"False". The
//     adjacent ToDict writes JSON's lowercase `true`, and both spellings
//     are right in their own place.
//
// QualitySummary is deliberately absent: it rides ToDict and not this.
//
// Named narrowing: StuckProjects is []string, so a report carrying
// `[1, None]` — which CPython renders verbatim, measured — cannot be
// represented. Python's annotation is List[str], every writer honours it,
// and widening this to []any would put a repr-any requirement on the whole
// package to model a shape nothing produces.
func (r Report) Summary() string {
	stuck := "none"
	if len(r.StuckProjects) > 0 {
		stuck = pyval.ReprStrings(r.StuckProjects)
	}
	lines := []string{
		"heartbeat run_id=" + r.RunID,
		"health=" + r.HealthStatus,
		"stuck_projects=" + stuck,
		"recovery_actions=" + pyval.Str(len(r.RecoveryActions)),
		"telegram_sent=" + pyval.Str(r.TelegramSent),
		"elapsed_ms=" + pyval.Str(r.ElapsedMS),
	}
	for _, a := range r.RecoveryActions {
		lines = append(lines, "  [tier"+pyval.Str(a.Tier)+"] "+a.Target+
			": "+a.Action+" → "+a.Outcome)
	}
	return strings.Join(lines, "\n")
}

// ToDict is HeartbeatReport.to_dict(), key order included — this row is
// appended to a JSONL ledger the Python runtime also writes and reads, so
// the order is part of the on-disk contract, not a rendering preference.
func (r Report) ToDict() pyval.Obj {
	acts := pyval.List{}
	for _, a := range r.RecoveryActions {
		o := pyval.Obj{}
		o.Set("tier", a.Tier)
		o.Set("target", a.Target)
		o.Set("action", a.Action)
		o.Set("outcome", a.Outcome)
		o.Set("detail", a.Detail)
		acts = append(acts, o)
	}
	// Python's default_factory=list means these fields are always a list,
	// never null. There is no nil-guard here because there is nothing to
	// guard: pyval renders a nil List as `[]` and a nil Obj as `{}`, so
	// `r.Checks` goes through untouched even when a caller left it unset.
	// A guard was written here first and the mutation battery classified
	// all three as EQUIVALENT — dead code that reads like a safety net.
	stuck := pyval.List{}
	for _, s := range r.StuckProjects {
		stuck = append(stuck, s)
	}
	out := pyval.Obj{}
	out.Set("run_id", r.RunID)
	out.Set("checked_at", r.CheckedAt)
	out.Set("health_status", r.HealthStatus)
	out.Set("checks", r.Checks)
	out.Set("stuck_projects", stuck)
	out.Set("recovery_actions", acts)
	out.Set("telegram_sent", r.TelegramSent)
	out.Set("elapsed_ms", r.ElapsedMS)
	out.Set("quality_summary", r.QualitySummary)
	return out
}

// checkDetail is `detail.startswith(...)`'s implicit precondition, made
// explicit. CPython raises before it looks at the check's NAME, so a
// non-string detail on a check with no recovery rule at all still raises —
// measured, and the reason this is a helper called first rather than a
// guard folded into the rule lookup.
func checkDetail(v any) (string, error) {
	if s, ok := v.(string); ok {
		return s, nil
	}
	return "", &pyval.PyErr{Class: "AttributeError", Msg: fmt.Sprintf(
		"'%s' object has no attribute 'startswith'", pyval.TypeName(v))}
}

// ---------------------------------------------------------------------------
// Tier 1 — scripted recovery
// ---------------------------------------------------------------------------

// tier1Rules is the `elif` chain as data, so the four messages sit in one
// place. Its iteration order is NOT the output order — see Tier1Scripted.
var tier1Rules = map[string]struct {
	action  string
	outcome string
}{
	"disk_space": {
		"Disk space low — recommend: rm old logs in workspace/output/",
		"suggested"},
	"llm_backend": {
		"No viable LLM backend lane — set an API key, install the claude CLI, " +
			"or fix llm.backend_order",
		"escalated"},
	"openclaw_gateway": {
		"OpenClaw gateway unreachable — check if openclaw service is running",
		"suggested"},
	"workspace_writable": {
		"Workspace not writable — check filesystem permissions",
		"escalated"},
}

// Tier1Scripted is heartbeat._tier1_scripted.
//
// Four things a reasonable port gets wrong, all measured:
//
//   - The output order is the CHECKS' insertion order, not a fixed rule
//     order. Python walks `checks.items()` and the `elif` chain only picks
//     the message. Feeding the same four checks reversed gives the four
//     actions reversed.
//   - The gate is `startswith`, not equality: "failure", "failed" and
//     "warning: slow" all fire, and a bare "fail" with no detail fires too.
//     It is also CASE-SENSITIVE — "FAIL: shouting" produces nothing.
//   - A failing check whose name has no rule produces NOTHING. The chain
//     has no else, so a check SystemHealth grows tomorrow is silently
//     unhandled rather than escalated. That is the behaviour; it is also
//     worth a bug report against the Python, which is filed rather than
//     fixed here — a port that fixes it stops being a port.
//   - A non-string detail RAISES, before the name is consulted.
func Tier1Scripted(checks pyval.Obj) ([]RecoveryAction, error) {
	var actions []RecoveryAction
	for _, f := range checks {
		detail, err := checkDetail(f.Val)
		if err != nil {
			return nil, err
		}
		if !strings.HasPrefix(detail, "fail") && !strings.HasPrefix(detail, "warn") {
			continue
		}
		rule, known := tier1Rules[f.Key]
		if !known {
			continue
		}
		actions = append(actions, RecoveryAction{
			Tier: 1, Target: f.Key, Action: rule.action,
			Outcome: rule.outcome, Detail: detail,
		})
	}
	return actions, nil
}

// ---------------------------------------------------------------------------
// Tier 2 — the cooldown (the call it guards is not ported)
// ---------------------------------------------------------------------------

// Cooldown is the per-project diagnosis cooldown, `_diagnosis_last_ran`.
//
// It is a VALUE, not a package global. Python's is module state, which is
// the right shape there and would make this untestable without a reset
// hook — and a reset hook is a second way to clear the map that production
// never uses, so it gets tested and the real path does not.
type Cooldown struct {
	last map[string]time.Duration
	// Now is the monotonic clock, seamed for tests. Nil means real time.
	Now func() time.Duration
}

func NewCooldown() *Cooldown { return &Cooldown{last: map[string]time.Duration{}} }

var processStart = time.Now()

func (c *Cooldown) now() time.Duration {
	if c.Now != nil {
		return c.Now()
	}
	return time.Since(processStart)
}

// Due is `_diagnosis_due`.
//
// A project never diagnosed is ALWAYS due, and that is an explicit branch
// rather than a zero default. Python's comment says why and it ports
// exactly: `time.monotonic()` counts from boot on Linux, so a 0.0 sentinel
// would suppress the first diagnosis for the first thirty minutes of system
// uptime — on a freshly booted box, or a CI runner, which is where nobody
// would look. Go's monotonic clock has the same hazard with a different
// origin.
func (c *Cooldown) Due(project string) bool {
	last, seen := c.last[project]
	if !seen {
		return true
	}
	return c.now()-last >= DiagnosisCooldown
}

// MarkRan is `_mark_diagnosis_ran`.
func (c *Cooldown) MarkRan(project string) { c.last[project] = c.now() }

// ---------------------------------------------------------------------------
// Tier 3 — escalation
// ---------------------------------------------------------------------------

// Tier3Escalate is heartbeat._tier3_escalate.
//
// send is the notify seam — Python's `telegram_notify`. A send that fails is
// swallowed to false with a line on stderr, exactly as Python's bare
// `except Exception` does, because a failed alert must not take down the
// heartbeat that noticed the problem. warn is that stderr seam; nil writes
// to os.Stderr.
//
// The gate is `status not in ("critical","degraded") AND no stuck projects`,
// so a HEALTHY report with a stuck project DOES send — and sends with the
// word HEALTHY in the subject line, which reads like a bug and is the
// measured behaviour. An unrecognised status sends too, upper-cased as-is.
//
// Named narrowing: Python returns `telegram_notify(message)` UNCHANGED, so
// a notifier answering a truthy string makes _tier3_escalate return that
// string despite its `-> bool` annotation (measured: "yes"). Here the seam
// is typed bool. Every real notifier returns a bool; the pass-through is an
// artifact of what a monkeypatch can do, not a behaviour anything relies on.
func Tier3Escalate(r Report, send func(string) (bool, error), warn func(string)) (bool, error) {
	if r.HealthStatus != "critical" && r.HealthStatus != "degraded" &&
		len(r.StuckProjects) == 0 {
		return false, nil
	}
	lines := []string{"\U0001F514 Maro Heartbeat Alert — " +
		strings.ToUpper(r.HealthStatus)}
	if len(r.StuckProjects) > 0 {
		lines = append(lines, "Stuck projects: "+
			strings.Join(r.StuckProjects, ", "))
	}
	// Tier 2 AND suggested — a tier-2 action that escalated is not listed,
	// and neither is a tier-1 suggestion. Capped at three.
	shown := 0
	for _, a := range r.RecoveryActions {
		if a.Tier != 2 || a.Outcome != "suggested" {
			continue
		}
		if shown == 3 {
			break
		}
		lines = append(lines, "  ["+a.Target+"] "+a.Action)
		shown++
	}
	// The failed-checks scan sits ABOVE Python's `try:`, so its
	// AttributeError propagates out of _tier3_escalate rather than becoming
	// the False the send failure produces. Read from the source and
	// confirmed by measurement: a check whose detail is an int raises here
	// instead of returning False.
	var failed []string
	for _, f := range r.Checks {
		s, err := checkDetail(f.Val)
		if err != nil {
			return false, err
		}
		if strings.HasPrefix(s, "fail") {
			failed = append(failed, f.Key)
		}
	}
	if len(failed) > 0 {
		lines = append(lines, "Failed checks: "+strings.Join(failed, ", "))
	}
	ok, err := send(strings.Join(lines, "\n"))
	if err != nil {
		if warn == nil {
			warn = func(s string) { fmt.Fprintln(os.Stderr, s) }
		}
		warn(fmt.Sprintf("[heartbeat] telegram escalation failed: %v", err))
		return false, nil
	}
	return ok, nil
}

// ---------------------------------------------------------------------------
// The heartbeat log
// ---------------------------------------------------------------------------

// LogPath is `memory_dir() / "heartbeat-log.jsonl"`.
func LogPath(ws string) string {
	return filepath.Join(ws, "memory", "heartbeat-log.jsonl")
}

// Log is heartbeat._log_heartbeat: append the report and answer with the
// path, or "" if anything at all went wrong.
//
// The blanket swallow is Python's and is kept — a heartbeat that cannot
// write its own log must still finish its tick and escalate, and the only
// caller prints the return value. The ERROR is returned alongside so a
// caller that wants it has it; Python's is gone for good. That is the one
// place here deliberately more informative than the original, and it cannot
// change a decision because nothing branches on it.
func Log(ws string, r Report) (string, error) {
	line, err := pyval.DumpsCompactPy(r.ToDict())
	if err != nil {
		return "", err
	}
	path := LogPath(ws)
	if err := record.AppendRawLine(path, []byte(line)); err != nil {
		return "", err
	}
	return path, nil
}

// ---------------------------------------------------------------------------
// Cadence resolvers
// ---------------------------------------------------------------------------

// ResolveShadowEvery is `_resolve_shadow_every`, ResolveBacklogEvery is its
// sibling. They differ in exactly two constants, and the difference is
// load-bearing at every input, so they are spelled out rather than shared
// behind one exported name:
//
//	                shadow      backlog
//	floor           max(0, n)   max(1, n)
//	default         0 (off)     5
//
// The floor and the default are NOT the same number for backlog, which is
// where a merged implementation goes wrong: an explicit `-3` clamps to 1,
// but an explicit `"abc"` raises inside int() and falls all the way back to
// 5. Measured, both columns:
//
//	shadow:  None->0  0->0  -3->0  False->0  2.9->2  "1_0"->10  "abc"->0
//	backlog: None->5  0->1  -3->1  False->1  2.9->2  "1_0"->10  "abc"->5
//
// explicit is `Optional[int]` in Python and `any` here, because the value
// arrives from a CLI flag and from config and CPython accepts anything
// int() accepts — a bool, a float truncated toward zero, a string with PEP
// 515 underscores. Pass nil for None.
//
// cfg is the config read, as a THUNK rather than a value: Python's sits
// inside the else branch, so a config backend that fails must not be
// consulted on a path that never reads it. Both an error and an unusable
// value land on the default, because Python's catch is a blanket
// `except Exception` wrapping the import, the get and the int() together.
// A nil cfg means "no config", which is the default.
func ResolveShadowEvery(explicit any, cfg func() (any, error)) int {
	return resolveEvery(explicit, cfg, 0, DefaultShadowEvery)
}

func ResolveBacklogEvery(explicit any, cfg func() (any, error)) int {
	return resolveEvery(explicit, cfg, 1, DefaultBacklogEvery)
}

func resolveEvery(explicit any, cfg func() (any, error), floor, def int) int {
	if explicit != nil {
		n, err := pyval.Int(explicit)
		if err != nil {
			return def
		}
		return max(floor, n)
	}
	if cfg == nil {
		return def
	}
	raw, err := cfg()
	if err != nil {
		return def
	}
	n, err := pyval.Int(raw)
	if err != nil {
		return def
	}
	return max(floor, n)
}
