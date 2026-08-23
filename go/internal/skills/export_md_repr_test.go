package skills

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// THE CONSEQUENCE TEST for adversarial r5's M2.
//
// pytext.Repr's defects were not a formatting nicety: this file is written
// by Go and parsed by Python. skill_loader._parse_frontmatter is not a YAML
// parser — it iterates `raw_meta.splitlines()` and splits each line on the
// first colon — so a RAW newline inside a trigger ends the `triggers:` line
// early, drops every trigger after it, and turns the remainder of the
// trigger text into a phantom frontmatter key. A raw NUL is not
// representable in the format at all.
//
// So the pin is the whole file through the real Python loader, and the
// authority is Python's OWN exporter: both runtimes build this line with
// repr(), so byte parity on it is the contract, not an approximation of one.
func pythonSkillMD(t *testing.T, name, description string, triggers []string) (string, []string) {
	t.Helper()
	src, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(src, "skill_loader.py")); err != nil {
		t.Skipf("Python source tree unavailable: %v", err)
	}
	payload, err := json.Marshal(map[string]any{
		"name": name, "description": description, "triggers": triggers})
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", pySkillMDProducer)
	cmd.Stdin = strings.NewReader(string(payload))
	cmd.Env = append(os.Environ(), "PYTHONPATH="+src)
	out, err := cmd.Output()
	if err != nil {
		var stderr string
		if ee, ok := err.(*exec.ExitError); ok {
			stderr = string(ee.Stderr)
		}
		t.Skipf("python3 unavailable or failed: %v\n%s", err, stderr)
	}
	var got struct {
		Line     string   `json:"line"`
		Triggers []string `json:"triggers"`
	}
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("decoding CPython output: %v\n%s", err, out)
	}
	return got.Line, got.Triggers
}

// Builds the triggers line the way Python's exporter does, then parses a
// file carrying it with Python's REAL loader.
const pySkillMDProducer = `
import json, sys, tempfile
from pathlib import Path
spec = json.load(sys.stdin)
import skill_loader
line = "triggers: [" + ", ".join(repr(t) for t in spec["triggers"][:8]) + "]"
d = Path(tempfile.mkdtemp(prefix="m2skill-"))
assert str(d).startswith("/tmp/"), d
p = d / "s.md"
p.write_text("---\nname: s\ndescription: \"%s\"\nroles_allowed: [worker]\n%s\n---\n\nbody\n"
             % (spec["description"], line), encoding="utf-8")
summary = skill_loader.load_skill_file(p)
print(json.dumps({"line": line, "triggers": list(summary.triggers)}))
`

// triggersLine pulls the frontmatter line out of a written SKILL.md.
func triggersLine(t *testing.T, body string) string {
	t.Helper()
	for _, l := range strings.Split(body, "\n") {
		if strings.HasPrefix(l, "triggers: ") {
			return l
		}
	}
	t.Fatalf("no triggers line in:\n%s", body)
	return ""
}

func TestSkillMarkdownTriggersMatchPythonsExporterByte(t *testing.T) {
	cases := [][]string{
		{"research", "investigate"},
		// The two defects, as trigger text. Without the fix the first loses
		// its backslash and the second puts a raw control character into the
		// file.
		{`it's a \ backslash`},
		{"tab\there", "esc\x1bhere", "nul\x00here"},
		// A raw newline is the one that BREAKS THE FORMAT rather than just
		// spelling it differently: the loader is line-based.
		{"first\nsecond", "after"},
		{"quote\"and'both", "u2028 sep", "astral \U0001d173"},
	}
	for _, triggers := range cases {
		ws := t.TempDir()
		s := base("sk-a", "Repr Probe")
		s.TriggerPatterns = triggers
		dest, err := ExportSkillAsMarkdown(ws, s, true)
		if err != nil {
			t.Fatal(err)
		}
		raw, err := os.ReadFile(dest)
		if err != nil {
			t.Fatal(err)
		}
		goLine := triggersLine(t, string(raw))
		wantLine, wantTriggers := pythonSkillMD(t, s.Name, s.Description, triggers)
		if goLine != wantLine {
			t.Errorf("triggers line differs from Python's exporter\n go: %q\nwant: %q",
				goLine, wantLine)
		}

		// And the file Go wrote must parse, through Python's real loader, to
		// what Python's own file parses to. A byte match on one line does not
		// by itself prove the FILE survived.
		gotTriggers := pythonLoadSkill(t, dest)
		if len(gotTriggers) != len(wantTriggers) {
			t.Errorf("Python's loader read %d trigger(s) from the Go file and "+
				"%d from its own: %q vs %q — the frontmatter did not survive",
				len(gotTriggers), len(wantTriggers), gotTriggers, wantTriggers)
			continue
		}
		for i := range gotTriggers {
			if gotTriggers[i] != wantTriggers[i] {
				t.Errorf("trigger %d: Python read %q from the Go file, %q from its own",
					i, gotTriggers[i], wantTriggers[i])
			}
		}
	}
}

// pythonLoadSkill runs Python's real skill_loader over a file Go wrote.
func pythonLoadSkill(t *testing.T, path string) []string {
	t.Helper()
	src, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c",
		"import json,sys;from pathlib import Path;import skill_loader;"+
			"s=skill_loader.load_skill_file(Path(sys.argv[1]));"+
			"print(json.dumps(list(s.triggers) if s else None))", path)
	cmd.Env = append(os.Environ(), "PYTHONPATH="+src)
	out, err := cmd.Output()
	if err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	var got []string
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("decoding CPython output: %v\n%s", err, out)
	}
	return got
}

// The narrow, direct statement of the format break, so a regression names
// itself rather than showing up as a trigger-count mismatch.
func TestATriggerNewlineNeverReachesTheFrontmatter(t *testing.T) {
	ws := t.TempDir()
	s := base("sk-a", "Repr Probe")
	s.TriggerPatterns = []string{"first\nsecond", "after"}
	dest, err := ExportSkillAsMarkdown(ws, s, true)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(dest)
	if err != nil {
		t.Fatal(err)
	}
	line := triggersLine(t, string(raw))
	if !strings.Contains(line, `\n`) {
		t.Errorf("the newline is not escaped in the triggers line: %q", line)
	}
	if !strings.Contains(line, "after") {
		t.Errorf("a trigger was lost off the end of the line: %q", line)
	}
}
