package worktree

import (
	"os"
	"regexp"
	"strings"
	"testing"
)

// The owner sidecar is a CROSS-RUNTIME file: a clone provisioned by one
// engine can be swept by the other, and the sweep reads it to decide
// whether to merge a repository back and delete it. Its bytes are the
// contract — key ORDER included, because json.dumps writes insertion
// order and a Go map[string]any through encoding/json writes alphabetical
// order, which is the port's oldest recurring divergence family.

const pySidecarSrc = `
import json, sys, logging
from pathlib import Path
import worktree

payload = json.loads(sys.argv[1])
WS = payload["ws"]

class _R:
    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err

rules = []
for r in payload["rules"]:
    rules.append((tuple(x.replace("{{WS}}", WS) for x in r["prefix"]),
                  r.get("rc", 0), r.get("stdout", ""), r.get("stderr", "")))

def fake_run(argv, capture_output=None, text=None, timeout=None, **kw):
    a = [str(x) for x in argv]
    for p, rc, out, err in rules:
        if len(p) <= len(a) and all(x == "*" or x == a[i] for i, x in enumerate(p)):
            return _R(rc, out, err)
    return _R(0, "", "")

worktree.subprocess.run = fake_run
logging.getLogger("maro.worktree").addHandler(logging.NullHandler())

c = worktree.provision_clone(payload["repo"], payload["name"], loop_id=payload["loop_id"])
side = Path(str(c.path) + ".owner.json")
print(json.dumps({"clone": str(c.path),
                  "raw": side.read_text(encoding="utf-8"),
                  "pid_matches": json.loads(side.read_text())["owner_pid"] == __import__("os").getpid(),
                  "mode": oct(side.stat().st_mode & 0o777)}))
`

var (
	rePID     = regexp.MustCompile(`"owner_pid": -?[0-9]+`)
	reStart   = regexp.MustCompile(`"owner_start": (null|"[0-9a-f]+")`)
	reCreated = regexp.MustCompile(`"created": [0-9eE.+-]+`)
)

// normalizeSidecar blanks the three fields that cannot agree between two
// processes — the pid, its birth token and the wall clock — and leaves
// EVERYTHING else, so the comparison still covers key order, the ": " and
// ", " separators json.dumps defaults to, and ensure_ascii.
func normalizeSidecar(s string) string {
	s = rePID.ReplaceAllString(s, `"owner_pid": <PID>`)
	s = reStart.ReplaceAllString(s, `"owner_start": <TOK>`)
	return reCreated.ReplaceAllString(s, `"created": <T>`)
}

func TestCloneOwnerSidecarBytesMatchCPython(t *testing.T) {
	const repo = "/srv/repo"
	pyWS := t.TempDir()
	goWS := t.TempDir()
	rules := []gitRule{
		rule(0, "true\n", "", "git", "-C", repo, "rev-parse", "--is-inside-work-tree"),
		rule(0, "main\n", "", "git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"),
	}
	// A name whose sanitised form carries a non-ASCII character, so the
	// comparison covers ensure_ascii's escaping too.
	const name = "jobé"

	var out struct {
		Clone      string `json:"clone"`
		Raw        string `json:"raw"`
		PIDMatches bool   `json:"pid_matches"`
		Mode       string `json:"mode"`
	}
	pyprobeWorktree(pyWS).RunJSON(t, pySidecarSrc, &out, pyprobeArg(t, map[string]any{
		"ws": pyWS, "rules": rules, "repo": repo, "name": name, "loop_id": "L9",
	}))

	// --- the CLAIM about CPython, before anything is compared ---
	if !out.PIDMatches {
		t.Fatal("CPython did not stamp its own pid — the fixture is not " +
			"exercising _write_clone_owner")
	}
	wantKeys := []string{"owner_pid", "owner_start", "repo_dir", "base_ref", "branch", "created"}
	if got := keyOrderOf(out.Raw); !equalStrings(got, wantKeys) {
		t.Fatalf("the claim about CPython's sidecar key order is stale.\n want %v\n got  %v",
			wantKeys, got)
	}
	// ensure_ascii defaults to TRUE, so the é is written as é and
	// not as its UTF-8 bytes. A port that emitted the literal character
	// would still be valid JSON and still parse back to the same string —
	// and would still be a different file on disk from the one the other
	// runtime writes.
	escaped := `"branch": "maro/L9/job` + string([]byte{'\\', 'u', '0', '0', 'e', '9'}) + `"`
	if !strings.Contains(out.Raw, escaped) {
		t.Fatalf("CPython's sidecar does not carry the ESCAPED branch name — "+
			"either ensure_ascii changed or the name stopped being non-ASCII.\n%s",
			out.Raw)
	}
	if strings.Contains(out.Raw, `":"`) || strings.Contains(out.Raw, `,"`) {
		t.Fatalf("CPython used compact separators; the claim that json.dumps' "+
			"DEFAULTS carry spaces is stale.\n%s", out.Raw)
	}

	// --- the comparison ---
	t.Setenv("MARO_WORKSPACE", goWS)
	var calls []recordedCall
	prev := subprocessRun
	subprocessRun = scriptRunnerWS(rules, goWS, &calls)
	defer func() { subprocessRun = prev }()

	clone, err := ProvisionClone(repo, name, "L9")
	if err != nil {
		t.Fatal(err)
	}
	if clone == nil {
		t.Fatal("Go returned no clone where CPython returned one")
	}
	raw, err := os.ReadFile(clone.Path + ownerSidecarSuffix)
	if err != nil {
		t.Fatalf("Go wrote no sidecar: %v", err)
	}
	if got, want := normalizeSidecar(string(raw)), normalizeSidecar(out.Raw); got != want {
		t.Errorf("sidecar bytes differ.\nCPython %s\nGo      %s", want, got)
	}
	// The normaliser must not be hiding a Go side that wrote nothing
	// there: assert the two volatile fields are real before trusting the
	// comparison that blanked them.
	if !strings.Contains(string(raw), `"owner_pid": `+itoa(os.Getpid())+`,`) {
		t.Errorf("Go did not stamp its own pid: %s", raw)
	}
	if strings.Contains(string(raw), `"owner_start": null`) {
		t.Errorf("Go wrote a null owner_start on a Linux box where CPython " +
			"produced a token — the normaliser would have hidden this")
	}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	if neg {
		return "-" + string(b)
	}
	return string(b)
}

var reKey = regexp.MustCompile(`"([a-z_]+)": `)

func keyOrderOf(s string) []string {
	var out []string
	for _, m := range reKey.FindAllStringSubmatch(s, -1) {
		out = append(out, m[1])
	}
	return out
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
