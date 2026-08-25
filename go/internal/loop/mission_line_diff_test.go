package loop

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyMissionLineSrc is `_recorded_mission`'s body, minus the workspace
// plumbing: read_text, splitlines, strip, the ">" test, and lstrip's CUTSET.
//
// The probe drives the algorithm rather than the function because
// `_orch().next_path(slug)` needs a live orch workspace, and standing one up
// would put MARO_WORKSPACE resolution between the fixture and the four rules
// that are actually under test. What it costs is stated: this pins the
// parse, not the path resolution.
const pyMissionLineSrc = `
import json, sys
_argv = json.loads(sys.argv[1])
try:
    text = open(_argv["path"], encoding="utf-8", newline=None).read()
except Exception:
    print(json.dumps({"mission": ""})); raise SystemExit
out = ""
for line in text.splitlines():
    line = line.strip()
    if line.startswith(">"):
        out = line.lstrip("> ").strip()
        break
print(json.dumps({"mission": out}))
`

// TestRecordedMissionMatchesCPython pins the value that decides WHICH
// PROJECT DIRECTORY a run writes into.
//
// Three idioms at one site, all three of the ones the graduation sweep
// classified — read_text (strict decode + universal newlines), splitlines()
// (ten separators, not one), and strip() (which knows U+001C–U+001F where
// TrimSpace does not) — plus lstrip's cutset, which is not a prefix strip.
//
// The consequence is not cosmetic: `resolveProjectSlug` compares this
// against the incoming goal to decide whether two goals that collide on
// slug are the same work. Get it wrong and a run adopts another run's
// artifacts as its own prior work, and then overwrites them.
func TestRecordedMissionMatchesCPython(t *testing.T) {
	cases := map[string]string{
		"plain":            "Mission:\n\n> port the graduation window\n",
		"crlf":             "Mission:\r\n\r\n> a CRLF ledger\r\n",
		"lone-cr":          "Mission:\r\r> an old-Mac ledger\r",
		"vt-before":        "Mission:\x0b> the VT hides this from a \\n split\n",
		"trailing-us":      "Mission:\n> a unit separator rides along\x1f\n",
		"leading-us":       "Mission:\n\x1f> and here it leads\n",
		"angle-run":        "Mission:\n>>>  quoted twice over\n",
		"space-then-angle": "Mission:\n   > indented\n",
		"no-marker":        "Mission:\n\nnothing quoted here\n",
		"empty":            "",
		"marker-only":      ">\n",
		"angles-only":      "> > >\n",
		// U+2028 is a splitlines() separator and NOT a strip() character,
		// so it moves the line boundary without touching the content.
		"para-sep": "Mission: > after a paragraph separator\n",
		// NEL, spelled \u — a raw 0x85 byte is not UTF-8 and the probe's
		// own JSON argument channel would replace it with U+FFFD before
		// Python saw it, so the fixture would measure the transport.
		"nel": "Mission:> after a NEL\n",
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			dir := t.TempDir()
			path := filepath.Join(dir, "NEXT.md")
			if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
				t.Fatal(err)
			}
			var want struct {
				Mission string `json:"mission"`
			}
			pyprobe.Probe{Stdlib: true}.RunJSON(t, pyMissionLineSrc, &want,
				pyprobe.Arg(t, map[string]any{"path": path}))
			if got := recordedMission(dir); got != want.Mission {
				t.Errorf("recordedMission = %q, CPython %q", got, want.Mission)
			}
		})
	}
}

// TestRecordedMissionRefusesAnUndecodableLedger pins read_text's other half.
//
// CPython's `_recorded_mission` wraps the read in a bare `except: return ""`,
// so an undecodable NEXT.md means NO mission is recorded and the caller
// falls back to slug-only disambiguation. A lenient `string(raw)` read on
// with U+FFFD substituted — and could then MATCH a different project, which
// is the direction that loses work.
func TestRecordedMissionRefusesAnUndecodableLedger(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "NEXT.md")
	// 0xC3 with no continuation byte: invalid continuation, mid-buffer.
	body := "Mission:\n> a mission with a torn byte \xc3 in it\n"
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	var want struct {
		Mission string `json:"mission"`
	}
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyMissionLineSrc, &want,
		pyprobe.Arg(t, map[string]any{"path": path}))
	if want.Mission != "" {
		t.Fatalf("CPython read the undecodable ledger (%q) — the premise of "+
			"this test has changed", want.Mission)
	}
	if got := recordedMission(dir); got != "" {
		t.Errorf("recordedMission = %q on an undecodable ledger, CPython \"\" "+
			"— a lenient read substitutes U+FFFD and can match the wrong "+
			"project", got)
	}
}
