package loopfinalize

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/orch"
	"github.com/slycrel/maro-orchestration/go/internal/runs"
)

// The risk mint is the one piece of loop_finalize with arithmetic in it,
// and every interesting rule it has is a rule about MALFORMED input: gaps
// that are a string instead of a list, a verdict row that is not an
// object, a file that is not UTF-8, a directory where a file should be.
// None of those are reachable from a unit test that hands the function a
// tidy dict, because the shapes only exist once JSON has been through
// json.loads — so the differential drives BOTH runtimes over a seeded
// workspace on disk and compares the RISKS.md that comes out of it.
//
// Both sides seed their own workspace from one declarative spec and read
// back the file each one wrote, so the comparison is bytes produced by
// each runtime's own path joining, mkdir and append semantics, not two
// renderings compared in memory. The only value normalized away is the
// wall-clock stamp append_section_lines writes, and it is normalized by
// the same regexp shape on both sides.

type mintScenario struct {
	Kind string `json:"kind"`
	Name string `json:"name"`

	// mint
	Project string `json:"project"`
	LoopID  string `json:"loop_id"`
	// RiskMint is a STRING, not a bool: the third value is "raise", and a
	// *bool would make the config read's own failure inexpressible.
	RiskMint      string `json:"risk_mint"`
	RunDirName    string `json:"run_dir_name"`
	WriteVerdicts bool   `json:"write_verdicts"`
	Verdicts      string `json:"verdicts"`
	VerdictsB64   string `json:"verdicts_b64"`
	VerdictsIsDir bool   `json:"verdicts_is_dir"`
	ScopeFailed   bool   `json:"scope_failed"`
	WriteRisks    bool   `json:"write_risks"`
	RisksPre      string `json:"risks_pre"`
	// RisksPreB64 exists because Path.write_text ENCODES: seeding the
	// not-UTF-8 fixture through the text path gave Python a valid
	// two-codepoint file and Go a two-byte invalid one, and the
	// differential reported a divergence the code did not have.
	RisksPreB64 string `json:"risks_pre_b64"`

	// lesson
	Which          string `json:"which"`
	FailureClass   string `json:"failure_class"`
	Action         string `json:"action"`
	Recommendation string `json:"recommendation"`

	// registry
	Ops []regOp `json:"ops"`
}

type regOp struct {
	Op     string `json:"op"`
	Handle string `json:"handle"`
	Fail   string `json:"fail"`
}

// stampRE matches append_section_lines' `## <utc iso>` block header. It is
// the probe's regexp written twice on purpose: a shared normalizer that
// both sides imported could not tell a MISSING stamp line from a present
// one, because it would be applied to two strings the same helper built.
var stampRE = regexp.MustCompile(`(?m)^## \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`)

// backslashReplace is bytes.decode("utf-8", "backslashreplace"): every
// byte that cannot start or continue a valid sequence is rendered \xNN.
// Distinct from errors="replace", which maps every bad byte to the same
// U+FFFD and would let a 0xff and a 0xfe compare equal.
func backslashReplace(b []byte) string {
	var sb strings.Builder
	for i := 0; i < len(b); {
		r, size := utf8.DecodeRune(b[i:])
		if r == utf8.RuneError && size <= 1 {
			fmt.Fprintf(&sb, "\\x%02x", b[i])
			i++
			continue
		}
		sb.Write(b[i : i+size])
		i += size
	}
	return sb.String()
}

func normalizeStamp(text string) string {
	return stampRE.ReplaceAllString(text, "## <STAMP>")
}

func runDirNameFor(loopID string) string {
	return filepath.Base(runs.Dir("ws", loopID))
}

// ---------------------------------------------------------------------
// fixtures
// ---------------------------------------------------------------------

func verdict(loopID string, body string) string {
	return `{"loop_id": ` + jsonStr(loopID) + `, ` + body + `}`
}

func jsonStr(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

func mintScenarios() []mintScenario {
	var out []mintScenario
	add := func(s mintScenario) {
		s.Kind = "mint"
		if s.Project == "" {
			s.Project = "proj"
		}
		if s.LoopID == "" {
			s.LoopID = "loop-aaaa1111"
		}
		if s.RiskMint == "" {
			s.RiskMint = "true"
		}
		// Every mint scenario gets a run dir unless it explicitly asked
		// for the missing-run case, which spells the name "".
		if s.RunDirName == "" && s.Name != "no-run-dir" {
			s.RunDirName = runDirNameFor(s.LoopID)
		}
		out = append(out, s)
	}
	const L = "loop-aaaa1111"

	// --- the guards, before anything is read ------------------------
	// These two bypass `add` on purpose: its defaults exist so every other
	// fixture is a full record, and the empty project/loop are exactly the
	// values a default would erase.
	out = append(out,
		mintScenario{Kind: "mint", Name: "empty-project", Project: "",
			LoopID: L, RiskMint: "true"},
		mintScenario{Kind: "mint", Name: "empty-loop-id", Project: "proj",
			LoopID: "", RiskMint: "true"},
		// The two halves of that guard need separate witnesses. Without
		// the project half, a run WITH gaps mints into `projects//` — so
		// the fixture has to carry gaps. Without the loop-id half, the
		// resolve step would return nothing anyway and the only visible
		// difference is that the config read happens at all, so that
		// fixture makes the config raise.
		mintScenario{Kind: "mint", Name: "empty-project-with-gaps",
			Project: "", LoopID: L, RiskMint: "true",
			RunDirName: runDirNameFor(L), WriteVerdicts: true,
			Verdicts: verdict(L, `"gaps": ["g one"]`)},
		mintScenario{Kind: "mint", Name: "empty-loop-id-raising-config",
			Project: "proj", LoopID: "", RiskMint: "raise"})
	add(mintScenario{Name: "no-run-dir"})
	add(mintScenario{Name: "risk-mint-off", RiskMint: "false",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ["g1"]`)})
	add(mintScenario{Name: "risk-mint-raises", RiskMint: "raise",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ["g1"]`)})

	// --- nothing to mint --------------------------------------------
	add(mintScenario{Name: "no-verdicts-file"})
	add(mintScenario{Name: "verdicts-empty-file",
		WriteVerdicts: true, Verdicts: ""})
	add(mintScenario{Name: "verdicts-blank-lines",
		WriteVerdicts: true, Verdicts: "\n   \n\t\n"})
	add(mintScenario{Name: "verdicts-not-json",
		WriteVerdicts: true, Verdicts: "not json at all\n"})
	add(mintScenario{Name: "verdicts-json-but-not-object",
		WriteVerdicts: true, Verdicts: "[1, 2, 3]\n\"a string\"\n5\nnull\n"})
	add(mintScenario{Name: "verdicts-other-loop",
		WriteVerdicts: true, Verdicts: verdict("loop-other", `"gaps": ["g1"]`)})
	add(mintScenario{Name: "verdicts-loop-id-not-a-string",
		WriteVerdicts: true, Verdicts: `{"loop_id": 7, "gaps": ["g1"]}`})
	add(mintScenario{Name: "gaps-missing",
		WriteVerdicts: true, Verdicts: verdict(L, `"skipped": false`)})
	add(mintScenario{Name: "gaps-null",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": null`)})
	add(mintScenario{Name: "gaps-empty-list",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": []`)})
	add(mintScenario{Name: "gaps-all-whitespace",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ["", "   ", "\t\n"]`)})

	// --- the happy paths --------------------------------------------
	add(mintScenario{Name: "one-gap",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ["missing tests"]`)})
	add(mintScenario{Name: "three-gaps",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"gaps": ["g one", "g two", "g three"]`)})
	add(mintScenario{Name: "four-gaps-omission-note",
		WriteVerdicts: true,
		Verdicts: verdict(L,
			`"gaps": ["g one", "g two", "g three", "g four"]`)})
	add(mintScenario{Name: "ten-gaps-omission-note",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"gaps": ["a","b","c","d","e","f","g","h","i","j"]`)})
	add(mintScenario{Name: "scope-sentinel-only", ScopeFailed: true})
	add(mintScenario{Name: "sentinel-steals-a-gap-slot", ScopeFailed: true,
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"gaps": ["g one", "g two", "g three"]`)})
	add(mintScenario{Name: "sentinel-and-two-gaps-exactly", ScopeFailed: true,
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ["g one", "g two"]`)})
	add(mintScenario{Name: "sentinel-and-one-gap", ScopeFailed: true,
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ["g one"]`)})

	// --- skipped verdicts -------------------------------------------
	add(mintScenario{Name: "skipped-true",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"skipped": true, "gaps": ["g1"]`)})
	add(mintScenario{Name: "skipped-truthy-string",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"skipped": "no", "gaps": ["g1"]`)})
	add(mintScenario{Name: "skipped-falsy-zero",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"skipped": 0, "gaps": ["g1"]`)})
	add(mintScenario{Name: "skipped-falsy-empty-list",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"skipped": [], "gaps": ["g1"]`)})
	add(mintScenario{Name: "skipped-truthy-list",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"skipped": [false], "gaps": ["g1"]`)})
	// A skipped verdict still lets the SENTINEL through: the two sources
	// are independent, and reading `skipped` as "mint nothing" would drop
	// a risk the closure verdict never had an opinion about.
	add(mintScenario{Name: "skipped-still-mints-sentinel", ScopeFailed: true,
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"skipped": true, "gaps": ["g1"]`)})

	// --- the shapes `gaps` is not supposed to be --------------------
	add(mintScenario{Name: "gaps-is-a-string",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": "abc"`)})
	add(mintScenario{Name: "gaps-is-a-dict",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"gaps": {"k one": 1, "k two": 2}`)})
	// The falsy non-iterables. `gaps or []` turns each of these into an
	// empty list BEFORE the loop, so they mint nothing — where the
	// truthy non-iterables below raise. Dropping the `or []` is
	// invisible without them.
	add(mintScenario{Name: "gaps-is-zero",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": 0`)})
	add(mintScenario{Name: "gaps-is-false",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": false`)})
	add(mintScenario{Name: "gaps-is-empty-string",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ""`)})
	add(mintScenario{Name: "gaps-is-empty-dict",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": {}`)})
	add(mintScenario{Name: "gaps-is-an-int",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": 5`)})
	add(mintScenario{Name: "gaps-is-a-float",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": 5.0`)})
	add(mintScenario{Name: "gaps-is-true",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": true`)})
	add(mintScenario{Name: "gaps-mixed-element-types",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"gaps": [1, 2.5, null, true, ["x"], {"y": 1}]`)})

	// --- gap text mangling ------------------------------------------
	add(mintScenario{Name: "gap-newlines-collapse",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"gaps": ["line one\n  line two\n\n\tline three"]`)})
	add(mintScenario{Name: "gap-leading-trailing-space",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ["   padded   "]`)})
	add(mintScenario{Name: "gap-only-newlines-is-dropped",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ["\n\n", "kept"]`)})
	add(mintScenario{Name: "gap-clipped-at-500",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"gaps": [`+jsonStr(strings.Repeat("x", 700))+`]`)})
	add(mintScenario{Name: "gap-clip-boundary-500",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"gaps": [`+jsonStr(strings.Repeat("y", 500))+`]`)})
	add(mintScenario{Name: "gap-clip-boundary-501",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"gaps": [`+jsonStr(strings.Repeat("z", 501))+`]`)})
	// clip() counts what Python counts. A 400-char gap of astral-plane
	// runes is 400 characters and 1600 bytes, and a byte-counting clip
	// would truncate it mid-rune.
	add(mintScenario{Name: "gap-clip-non-ascii",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"gaps": [`+jsonStr(strings.Repeat("🙂", 400))+`]`)})
	add(mintScenario{Name: "gap-markdown-bullet-looking",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"gaps": ["- not a bullet\n- neither is this"]`)})

	// --- last matching row wins -------------------------------------
	add(mintScenario{Name: "last-matching-row-wins",
		WriteVerdicts: true,
		Verdicts: verdict(L, `"gaps": ["first"]`) + "\n" +
			verdict("loop-other", `"gaps": ["noise"]`) + "\n" +
			verdict(L, `"gaps": ["second"]`) + "\n"})
	add(mintScenario{Name: "last-matching-row-is-skipped",
		WriteVerdicts: true,
		Verdicts: verdict(L, `"gaps": ["first"]`) + "\n" +
			verdict(L, `"skipped": true, "gaps": ["second"]`) + "\n"})
	add(mintScenario{Name: "trailing-garbage-after-good-row",
		WriteVerdicts: true,
		Verdicts:      verdict(L, `"gaps": ["kept"]`) + "\nnot json\n{oops\n"})

	// --- the file itself is wrong -----------------------------------
	add(mintScenario{Name: "verdicts-is-a-directory", VerdictsIsDir: true})
	add(mintScenario{Name: "verdicts-not-utf8", WriteVerdicts: true,
		VerdictsB64: base64.StdEncoding.EncodeToString(
			[]byte("{\"loop_id\": \"" + L + "\", \"gaps\": [\"\xff\xfe\"]}\n"))})
	add(mintScenario{Name: "verdicts-crlf", WriteVerdicts: true,
		Verdicts: verdict(L, `"gaps": ["g one"]`) + "\r\n"})

	// --- the idempotence pre-check ----------------------------------
	add(mintScenario{Name: "risks-already-names-loop",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ["g one"]`),
		WriteRisks: true,
		RisksPre:   "# RISKS\n\n## 2020-01-01T00:00:00Z\n- Open gap from run " + L + " (closure): old\n"})
	add(mintScenario{Name: "risks-names-loop-in-passing",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ["g one"]`),
		WriteRisks: true,
		RisksPre:   "# RISKS\n\nsomeone wrote " + L + " in a sentence\n"})
	add(mintScenario{Name: "risks-exists-without-loop",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ["g one"]`),
		WriteRisks: true,
		RisksPre:   "# RISKS\n\n## 2020-01-01T00:00:00Z\n- an older risk\n"})
	add(mintScenario{Name: "risks-empty-file",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ["g one"]`),
		WriteRisks: true, RisksPre: ""})
	// The pre-check reads the file. A RISKS.md that is not UTF-8 raises
	// out of the guard, not out of the append.
	add(mintScenario{Name: "risks-not-utf8-is-a-warning",
		WriteVerdicts: true, Verdicts: verdict(L, `"gaps": ["g one"]`),
		WriteRisks: true, RisksPreB64: base64.StdEncoding.EncodeToString(
			[]byte("# RISKS\n\xff\xfe\n"))})

	// --- loop ids that are awkward to substring-match ----------------
	add(mintScenario{Name: "loop-id-is-a-substring-of-another",
		LoopID:        "loop-ab",
		WriteVerdicts: true, Verdicts: verdict("loop-ab", `"gaps": ["g one"]`),
		WriteRisks: true,
		RisksPre:   "# RISKS\n\n- Open gap from run loop-abcdef (closure): x\n"})

	return out
}

func lessonScenarios() []mintScenario {
	mk := func(name, which, fc, action, rec string) mintScenario {
		return mintScenario{Kind: "lesson", Name: name, Which: which,
			FailureClass: fc, Action: action, Recommendation: rec}
	}
	return []mintScenario{
		mk("lesson-recovery-plain", "recovery", "timeout",
			"retry the step with a smaller budget", ""),
		mk("lesson-recovery-empty", "recovery", "", "", ""),
		mk("lesson-recovery-multiline", "recovery", "parse\nerror",
			"do\tthis\nthen that", ""),
		mk("lesson-recovery-unicode", "recovery", "échec",
			"réessayer — 🙂", ""),
		mk("lesson-auto-plain", "auto", "flaky-tool", "",
			"pin the tool version"),
		mk("lesson-auto-empty", "auto", "", "", ""),
		mk("lesson-auto-colon-heavy", "auto", "a: b: c", "",
			"x: y: z"),
	}
}

func registryScenarios() []mintScenario {
	mk := func(name string, ops ...regOp) mintScenario {
		return mintScenario{Kind: "registry", Name: name, Ops: ops}
	}
	d := func(h, fail string) regOp { return regOp{Op: "defer", Handle: h, Fail: fail} }
	x := func(h string) regOp { return regOp{Op: "drain", Handle: h} }
	return []mintScenario{
		mk("reg-drain-unknown", x("reg-a-h1")),
		mk("reg-one-ok", d("reg-b-h1", ""), x("reg-b-h1")),
		mk("reg-drain-twice-is-zero", d("reg-c-h1", ""), x("reg-c-h1"),
			x("reg-c-h1")),
		mk("reg-three-in-order", d("reg-d-h1", ""), d("reg-d-h1", ""),
			d("reg-d-h1", ""), x("reg-d-h1")),
		// The pop happens BEFORE the loop, so a raise cannot strand a
		// half-drained entry for a second drain to re-run.
		mk("reg-raise-still-counts", d("reg-e-h1", "boom one"),
			d("reg-e-h1", ""), x("reg-e-h1"), x("reg-e-h1")),
		mk("reg-all-raise", d("reg-f-h1", "one"), d("reg-f-h1", "two"),
			x("reg-f-h1")),
		mk("reg-handles-are-independent", d("reg-g-h1", ""),
			d("reg-g-h2", "h2 boom"), x("reg-g-h2"), x("reg-g-h1")),
		mk("reg-empty-handle-id", d("", ""), x("")),
	}
}

func allMintScenarios() []mintScenario {
	out := mintScenarios()
	out = append(out, lessonScenarios()...)
	out = append(out, registryScenarios()...)
	return out
}

// ---------------------------------------------------------------------
// the two runners
// ---------------------------------------------------------------------

func seedWorkspace(t *testing.T, ws string, s mintScenario) {
	t.Helper()
	must := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	must(os.MkdirAll(filepath.Join(ws, "runs"), 0o755))
	if s.RunDirName != "" {
		build := filepath.Join(ws, "runs", s.RunDirName, "build")
		must(os.MkdirAll(build, 0o755))
		vp := filepath.Join(build, "closure_verdicts.jsonl")
		switch {
		case s.VerdictsIsDir:
			must(os.Mkdir(vp, 0o755))
		case s.WriteVerdicts && s.VerdictsB64 != "":
			blob, err := base64.StdEncoding.DecodeString(s.VerdictsB64)
			must(err)
			must(os.WriteFile(vp, blob, 0o644))
		case s.WriteVerdicts:
			must(os.WriteFile(vp, []byte(s.Verdicts), 0o644))
		}
		if s.ScopeFailed {
			must(os.WriteFile(filepath.Join(build, "scope-raw-FAILED.txt"),
				[]byte("raw"), 0o644))
		}
	}
	if s.WriteRisks {
		rp := filepath.Join(ws, "projects", s.Project, "RISKS.md")
		must(os.MkdirAll(filepath.Dir(rp), 0o755))
		body := []byte(s.RisksPre)
		if s.RisksPreB64 != "" {
			b, err := base64.StdEncoding.DecodeString(s.RisksPreB64)
			must(err)
			body = b
		}
		must(os.WriteFile(rp, body, 0o644))
	}
}

func goMintRecord(t *testing.T, root string, s mintScenario) map[string]any {
	t.Helper()
	switch s.Kind {
	case "lesson":
		text := AutoDiagnosisLessonText(s.FailureClass, s.Recommendation)
		if s.Which == "recovery" {
			text = RecoveryPlanLessonText(s.FailureClass, s.Action)
		}
		return map[string]any{"name": s.Name, "text": text}
	case "registry":
		var reg MaintenanceRegistry
		warns := []string{}
		drains := []int{}
		for _, op := range s.Ops {
			if op.Op == "defer" {
				fail := op.Fail
				reg.DeferPostNotify(op.Handle, func() error {
					if fail != "" {
						return errors.New(fail)
					}
					return nil
				})
				continue
			}
			drains = append(drains, reg.DrainDeferred(op.Handle,
				func(msg string) { warns = append(warns, msg) }))
		}
		return map[string]any{"name": s.Name, "drains": drains,
			"warnings": warns}
	}

	ws := filepath.Join(root, s.Name)
	assertNotLiveStore(t, ws)
	if err := os.MkdirAll(ws, 0o755); err != nil {
		t.Fatal(err)
	}
	seedWorkspace(t, ws, s)

	warns := []string{}
	deps := MintDeps{
		RiskMintEnabled: func() (bool, error) {
			if s.RiskMint == "raise" {
				return false, errors.New("config exploded")
			}
			return s.RiskMint == "true", nil
		},
		Warning: func(msg string) { warns = append(warns, msg) },
	}
	n := MintRunRisksToProject(ws, s.Project, s.LoopID, deps)

	body, exists := "", false
	if s.Project != "" {
		if b, err := os.ReadFile(orch.RisksPath(ws, s.Project)); err == nil {
			body, exists = normalizeStamp(backslashReplace(b)), true
		}
	}
	// See the probe: only the workspace ROOT is normalized, so the tail
	// of every path an OSError names is still compared.
	for i, w := range warns {
		warns[i] = strings.ReplaceAll(w, ws, "<WS>")
	}
	return map[string]any{"name": s.Name, "minted": n, "risks_exists": exists,
		"risks": body, "warnings": warns}
}

// assertNotLiveStore is the tripwire from 2026-08-16, when a probe that
// mis-resolved its workspace overwrote a live ledger. Every path this test
// writes is asserted to be outside ~/.maro BEFORE the first mkdir.
func assertNotLiveStore(t *testing.T, path string) {
	t.Helper()
	abs, err := filepath.Abs(path)
	if err != nil {
		t.Fatal(err)
	}
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	live := filepath.Join(home, ".maro")
	if abs == live || strings.HasPrefix(abs, live+string(filepath.Separator)) {
		t.Fatalf("refusing to write inside the live store: %s", abs)
	}
}

func runMintProbe(t *testing.T, dir string, scs []mintScenario) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	specPath := filepath.Join(dir, "mint-scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	pyRoot := filepath.Join(dir, "py")
	assertNotLiveStore(t, pyRoot)
	if err := os.MkdirAll(pyRoot, 0o755); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "riskmint_probe.py.tpl", srcDirLF(t),
		pyRoot, specPath)
	// The probe seeds MARO_WORKSPACE per scenario. Clearing the inherited
	// value first means a host that happens to export one cannot make the
	// probe agree with Go by accident.
	cmd.Env = append(os.Environ(), "MARO_WORKSPACE=", "OPENCLAW_WORKSPACE=",
		"WORKSPACE_ROOT=", "MARO_ORCH_ROOT=")
	out, err := cmd.Output()
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			t.Fatalf("probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("probe failed: %v", err)
	}
	var recs []map[string]any
	if err := json.Unmarshal(out, &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

func TestRiskMintMatchesCPython(t *testing.T) {
	scs := allMintScenarios()
	dir := t.TempDir()
	pyRecs := runMintProbe(t, dir, scs)
	if len(pyRecs) != len(scs) {
		t.Fatalf("probe returned %d records for %d scenarios",
			len(pyRecs), len(scs))
	}
	goRoot := filepath.Join(dir, "go")
	if err := os.MkdirAll(goRoot, 0o755); err != nil {
		t.Fatal(err)
	}
	for i, s := range scs {
		t.Run(s.Name, func(t *testing.T) {
			got := goMintRecord(t, goRoot, s)
			want := pyRecs[i]
			if want["name"] != s.Name {
				t.Fatalf("record %d is %v, want %s", i, want["name"], s.Name)
			}
			a, _ := json.MarshalIndent(canonMint(got), "", "  ")
			b, _ := json.MarshalIndent(canonMint(want), "", "  ")
			if string(a) != string(b) {
				t.Errorf("go:\n%s\npy:\n%s", a, b)
			}
		})
	}
}

// canonMint re-marshals both records through the same generic shape so an
// int on one side and a float64 on the other are compared as the numbers
// they are, not as the Go types json.Unmarshal happened to pick.
func canonMint(m map[string]any) map[string]any {
	blob, _ := json.Marshal(m)
	var out map[string]any
	_ = json.Unmarshal(blob, &out)
	return out
}

func TestMintScenarioNamesAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for _, s := range allMintScenarios() {
		if seen[s.Name] {
			t.Errorf("duplicate scenario name %q", s.Name)
		}
		seen[s.Name] = true
	}
}

// The cap is on RISK LINES, and the omission note is not one of them: a
// run with a sentinel and four gaps mints two gaps, the sentinel AND the
// note, which is four lines under a constant named RiskLinesCap = 3. That
// is Python's behaviour and this port reproduces it; the assertion exists
// so the next reader meets it as a documented shape rather than as a
// suspected off-by-one in the cap arithmetic.
func TestOmissionNoteRidesOutsideTheCap(t *testing.T) {
	dir := t.TempDir()
	ws := filepath.Join(dir, "ws")
	assertNotLiveStore(t, ws)
	s := mintScenario{Name: "ws", Project: "proj", LoopID: "loop-aaaa1111",
		RiskMint: "true", RunDirName: runDirNameFor("loop-aaaa1111"),
		ScopeFailed: true, WriteVerdicts: true,
		Verdicts: verdict("loop-aaaa1111", `"gaps": ["a","b","c","d"]`)}
	if err := os.MkdirAll(ws, 0o755); err != nil {
		t.Fatal(err)
	}
	seedWorkspace(t, ws, s)
	n := MintRunRisksToProject(ws, s.Project, s.LoopID, MintDeps{
		RiskMintEnabled: func() (bool, error) { return true, nil },
		Warning:         func(string) {},
	})
	if n != 4 {
		t.Fatalf("minted %d lines, want 4 (2 gaps + sentinel + note)", n)
	}
	b, err := os.ReadFile(orch.RisksPath(ws, s.Project))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(b), "(2 more closure gap(s)") {
		t.Fatalf("omission note missing or miscounted:\n%s", b)
	}
}

// MintRunRisksToProject never returns an error and never panics, whatever
// the deps do. The Python contract is "never raises" and the reason is
// that risk minting is observability-grade: a mint that propagated would
// perturb result delivery.
func TestMintNeverPropagates(t *testing.T) {
	dir := t.TempDir()
	assertNotLiveStore(t, dir)
	warns := []string{}
	n := MintRunRisksToProject(dir, "proj", "loop-aaaa1111", MintDeps{
		RiskMintEnabled: func() (bool, error) {
			return true, errors.New("nope")
		},
		Warning: func(m string) { warns = append(warns, m) },
	})
	if n != 0 {
		t.Fatalf("minted %d, want 0", n)
	}
	want := "risk mint failed for loop loop-aaaa1111: nope"
	if len(warns) != 1 || warns[0] != want {
		t.Fatalf("warnings %q, want [%q]", warns, want)
	}
}
