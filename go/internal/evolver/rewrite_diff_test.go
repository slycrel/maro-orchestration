package evolver

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// pyRewriteSrc runs ONE store-rewriting verb and hands back the resulting
// store BYTES.
//
// Bytes, not parsed rows, because these are read-modify-WRITE callbacks and
// what they do with a line they are not interested in IS the output. Every
// divergence this file was written for — a dropped blank line, a trimmed
// preserved row, a differently-placed line boundary, a one-byte empty-store
// result — is invisible to a comparison that parses first.
const pyRewriteSrc = `
import json, sys
from pathlib import Path
import evolver_store

_argv = json.loads(sys.argv[1])
verb, sid = _argv["verb"], _argv["id"]
err = ""
try:
    if verb == "dismiss":
        ret = evolver_store.dismiss_suggestion(sid, _argv.get("reason", ""))
    elif verb == "stamp":
        ret = evolver_store.stamp_verification(
            sid, verdict=_argv.get("verdict"), verified_at=_argv.get("at"),
            extensions=_argv.get("ext"))
    elif verb == "revert":
        ret = evolver_store.revert_suggestion(sid)
    elif verb == "apply":
        ret = evolver_store.apply_suggestion(sid, True)
    else:
        raise SystemExit("unknown verb %s" % verb)
except Exception as exc:
    ret, err = None, "%s: %s" % (type(exc).__name__, exc)

stores = {}
for rel in ("memory/suggestions.jsonl", "memory/dynamic-constraints.jsonl"):
    p = Path(_argv["ws"]) / rel
    # latin-1, so the comparison is over BYTES. utf-8-with-replace would
    # compare two lossy renderings and call them equal.
    stores[rel] = p.read_bytes().decode("latin-1") if p.exists() else None

print(json.dumps({"ret": ret if isinstance(ret, (bool, type(None))) else True,
                  "err": err, "stores": stores}))
`

type rewriteProbe struct {
	Ret    any                `json:"ret"`
	Err    string             `json:"err"`
	Stores map[string]*string `json:"stores"`
}

// seedRewriteStore writes a suggestions ledger whose shape is ordinary
// except for the things a rewrite can destroy.
//
// Built from escapes, never typed: a raw control byte is invisible in a
// diff, and an editor that strips one turns this into a fixture that pins
// nothing.
func seedRewriteStore(t *testing.T, ws string) {
	t.Helper()
	row := func(id, cat, sug, extra string) string {
		return `{"suggestion_id":"` + id + `","category":"` + cat +
			`","target":"all","suggestion":"` + sug + `","confidence":0.8` +
			extra + `}`
	}
	w := func(rel, body string) {
		p := filepath.Join(ws, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	w("memory/suggestions.jsonl",
		row("s1", "new_guardrail", "the target row", `,"applied":true`)+"\n"+
			// A BLANK LINE. Not exotic at all, and it is the whole finding
			// for two of the five merges: CPython preserves it, a
			// skip-blanks rewrite deletes it.
			"\n"+
			// A row with LEADING AND TRAILING SPACES around it. Four merges
			// re-emit the stripped line and two re-emit the raw one, so
			// this line's fate is a per-merge fact, not a global one.
			"  "+row("s2", "prompt_tweak", "a padded neighbour", "")+"  \n"+
			// A line that splitlines() breaks and a "\n" split does not.
			// CPython's rewrite emits TWO lines here; a "\n" split emits
			// one, so the stores diverge in line COUNT after one rewrite.
			row("s3", "prompt_tweak", "a\x0bb", "")+"\n"+
			// A row that is only a unit separator: blank to str.strip(),
			// non-blank to strings.TrimSpace.
			"\x1f\n"+
			// Unparseable. Every merge has a preserve branch for it, and
			// the branches disagree about whether it comes back stripped.
			"  not json at all  \n"+
			row("s4", "prompt_tweak", "the last row", "")+"\n"+
			// An UNAPPLIED guardrail with a real regex pattern: the only
			// row in this fixture an apply can actually act on, and the
			// one that makes the dynamic-constraints append happen.
			`{"suggestion_id":"s5","category":"new_guardrail","target":"all",`+
			`"suggestion":"never rm -rf a workspace","confidence":0.9,`+
			`"pattern":"rm -rf","applied":false}`+"\n")
	w("memory/dynamic-constraints.jsonl",
		`{"source":"evolver:s1","pattern":"the target row","risk":"MEDIUM"}`+"\n"+
			"\n"+
			`{"source":"evolver:other","pattern":"a neighbour"}`+"\n"+
			"  not json  \n")
	// The change_log is what makes a revert HAPPEN. Without a matching
	// entry both runtimes take the "not found in change_log" exit, leave
	// both stores untouched, and the byte comparison passes by comparing
	// two unmodified files against each other.
	//
	// That is not a hypothetical: the first version of this fixture
	// omitted it, TestRevertRewritesBothStoresLikeCPython went green, and
	// the mutation battery reported MISS for both _mark_reverted mutants —
	// which is the only reason the vacuity was caught. A test reporting
	// agreement may be testing nothing.
	w("memory/change_log.jsonl",
		`{"ts":"2026-08-20T12:00:00+00:00","action":"apply_suggestion",`+
			`"category":"new_guardrail","suggestion_id":"s1","target":"all",`+
			`"confidence":0.8,"suggestion_text":"the target row",`+
			`"suggestion_hash":"abc123abc123",`+
			`"before_state":{"type":"guardrail_append"}}`+"\n")
}

func runRewriteBoth(t *testing.T, arg map[string]any, act func(ws string)) {
	t.Helper()
	pyWS, goWS := t.TempDir(), t.TempDir()
	seedRewriteStore(t, pyWS)
	seedRewriteStore(t, goWS)

	var want rewriteProbe
	arg["ws"] = pyWS
	pyprobe.Probe{Marker: "evolver_store.py", Workspace: pyWS}.RunJSON(
		t, pyRewriteSrc, &want, pyprobe.Arg(t, arg))
	if want.Err != "" {
		t.Fatalf("CPython raised %s — the fixture is not exercising the verb", want.Err)
	}

	act(goWS)

	for rel, wantBody := range want.Stores {
		got, rerr := os.ReadFile(filepath.Join(goWS, filepath.FromSlash(rel)))
		if wantBody == nil {
			if rerr == nil {
				t.Errorf("%s: the port wrote a store CPython did not", rel)
			}
			continue
		}
		if rerr != nil {
			t.Errorf("%s: CPython wrote this store and the port did not: %v", rel, rerr)
			continue
		}
		// The probe widened each byte to a rune via latin-1; narrow back.
		var wb []byte
		for _, r := range *wantBody {
			wb = append(wb, byte(r))
		}
		if maskStamps(string(got)) != maskStamps(string(wb)) {
			t.Errorf("%s: rewritten store differs.\n  go: %q\n  py: %q",
				rel, maskStamps(string(got)), maskStamps(string(wb)))
		}
	}
}

// stampRe matches an ISO timestamp VALUE, and maskStamps blanks it.
//
// The two runs happen microseconds apart, so a wall-clock field can never
// match byte for byte and the rewrite comparison would be untestable
// without this. What it costs is stated rather than hidden: this test can
// no longer see the stamp's FORMAT, which is a real divergence class (the
// port wrote RFC3339Nano where Python writes isoformat — "Z" instead of
// "+00:00", nine fractional digits instead of six).
//
// So the format gets its own differential — TestNowISOMatchesPythonIsoformat
// — rather than being silently surrendered here. A mask that removes a
// property without replacing the coverage is how a suite starts agreeing
// with itself.
var stampRe = regexp.MustCompile(
	`\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})`)

// epochRe matches the `added_at` epoch float, which is a wall clock in the
// other of its two spellings. Masked for the same reason and with the same
// obligation: the PRECISION it hides is pinned by the assertion at the end
// of TestApplyRewritesTheStoreLikeCPython.
var epochRe = regexp.MustCompile(`"added_at": \d+(\.\d+)?`)

func maskStamps(s string) string {
	return epochRe.ReplaceAllString(stampRe.ReplaceAllString(s, "<TS>"),
		`"added_at": <EPOCH>`)
}

// TestNowISOMatchesPythonIsoformat pins the format the byte comparison
// masks away.
//
// It is a FORMAT test, not a value test: the two clocks cannot agree, but
// the shape must — same separator, same offset spelling, same fractional
// width, and the same disappearance of the fraction when it is zero
// (which isoformat does and a fixed-width Go layout does not).
func TestNowISOMatchesPythonIsoformat(t *testing.T) {
	const src = `
import json, sys
from datetime import datetime, timezone, timedelta
out = []
for us in json.loads(sys.argv[1])["micros"]:
    t = datetime(2026, 8, 24, 5, 9, 32, us, tzinfo=timezone.utc)
    out.append(t.isoformat())
print(json.dumps(out))
`
	micros := []int{0, 1, 599972, 500000, 999999}
	var want []string
	pyprobe.Probe{Stdlib: true}.RunJSON(t, src, &want,
		pyprobe.Arg(t, map[string]any{"micros": micros}))
	for i, us := range micros {
		got := pyval.NowISO(time.Date(2026, 8, 24, 5, 9, 32, us*1000, time.UTC))
		if got != want[i] {
			t.Errorf("NowISO(%d µs) = %q, CPython isoformat %q", us, got, want[i])
		}
	}
	// And the store's own stamp must BE that helper, not a second
	// spelling of it: the whole finding was a local nowISO in this file
	// rendering RFC3339Nano into a store CPython reads.
	if s := nowISO(); !stampRe.MatchString(s) || strings.HasSuffix(s, "Z") {
		t.Errorf("nowISO() = %q — a \"Z\" suffix is RFC3339Nano, not isoformat", s)
	}
}

// TestDismissRewritesTheStoreLikeCPython — the plainest of the five merges,
// and the one whose Python shape the other three share.
func TestDismissRewritesTheStoreLikeCPython(t *testing.T) {
	runRewriteBoth(t, map[string]any{
		"verb": "dismiss", "id": "s4", "reason": "not now",
	}, func(ws string) {
		if _, err := Dismiss(ws, "s4", "not now"); err != nil {
			t.Fatal(err)
		}
	})
}

// TestStampVerificationRewritesTheStoreLikeCPython — same shape, different
// mutation, and it stamps a row that is NOT adjacent to the exotic lines.
func TestStampVerificationRewritesTheStoreLikeCPython(t *testing.T) {
	verdict, at := "confirmed", "2026-08-24T00:00:00+00:00"
	runRewriteBoth(t, map[string]any{
		"verb": "stamp", "id": "s2", "verdict": verdict, "at": at,
	}, func(ws string) {
		StampVerificationChanged(ws, "s2", VerificationStamp{
			Verdict: &verdict, VerifiedAt: &at})
	})
}

// TestRevertRewritesBothStoresLikeCPython drives the TWO odd merges at once
// — the only two of the five that neither strip nor skip blanks.
//
// `_drop_constraint` re-emits every surviving line RAW, and `_mark_reverted`
// re-emits every UNPARSEABLE line raw and returns `"\n".join(...) + "\n"`
// unconditionally. The port had normalized both into the majority shape, so
// it deleted blank lines and trimmed whitespace off rows it was preserving
// — a byte divergence in two shared stores needing no exotic character at
// all, just an ordinary blank line.
func TestRevertRewritesBothStoresLikeCPython(t *testing.T) {
	runRewriteBoth(t, map[string]any{
		"verb": "revert", "id": "s1",
	}, func(ws string) {
		Revert(ws, record.New(ws), "s1")
	})
}

// TestMarkRevertedWritesANewlineOnAnEmptyStore pins the one-byte case the
// byte comparison above cannot reach: `_mark_reverted` alone returns
// `"\n".join([]) + "\n"` — which is "\n" — where its four siblings return
// "".
//
// It looks like a typo, which is exactly why it needs a test: the next
// reader who tidies pyJoinAlways and pyJoinOrEmpty into one function has
// reintroduced the divergence, and nothing else would notice.
func TestMarkRevertedWritesANewlineOnAnEmptyStore(t *testing.T) {
	if got := pyJoinAlways(nil); got != "\n" {
		t.Errorf("pyJoinAlways(nil) = %q, CPython's `\"\\n\".join([]) + \"\\n\"` "+
			"is \"\\n\"", got)
	}
	if got := pyJoinOrEmpty(nil); got != "" {
		t.Errorf("pyJoinOrEmpty(nil) = %q, CPython's `... if out else \"\"` "+
			"is \"\"", got)
	}
	// And they must NOT agree on the non-empty case either way round.
	if pyJoinAlways([]string{"a"}) != pyJoinOrEmpty([]string{"a"}) {
		t.Error("the two joins disagree on a non-empty list; only the empty " +
			"case is supposed to differ")
	}
}

// TestRMWLinesIsTheOneLineRule pins the shared helper the seven callbacks
// now route through, and pins it against the property that matters: a
// rewrite must reflow a store the same way CPython's merge does.
//
// A per-callback test cannot show this. Each one would pass with its own
// consistent-but-wrong split, and the disagreement only appears when one
// runtime's rewrite is read by the other.
func TestRMWLinesIsTheOneLineRule(t *testing.T) {
	const src = `
import json, sys
print(json.dumps(json.loads(sys.argv[1])["text"].splitlines()))
`
	for name, text := range map[string]string{
		"blank-lines":   "a\n\nb\n",
		"no-trailing":   "a\nb",
		"vt":            "a\x0bb\n",
		"fs-gs-rs":      "a\x1cb\x1dc\x1ed\n",
		"us-not-a-sep":  "a\x1fb\n",
		"crlf":          "a\r\nb\r\n",
		"lone-cr":       "a\rb",
		"empty":         "",
		"only-newlines": "\n\n\n",
	} {
		t.Run(name, func(t *testing.T) {
			var want []string
			pyprobe.Probe{Stdlib: true}.RunJSON(t, src, &want,
				pyprobe.Arg(t, map[string]any{"text": text}))
			got := rmwLines(text)
			if len(got) != len(want) {
				t.Fatalf("%d lines %q, CPython %d %q", len(got), got, len(want), want)
			}
			for i := range got {
				if got[i] != want[i] {
					t.Errorf("line %d = %q, CPython %q", i, got[i], want[i])
				}
			}
		})
	}
}

// TestContentKeyCoercesLikePython — the dedup identity.
//
// Python's key is a tuple of three str() COERCIONS with the last stripped.
// The port asserted `.(string)`, which yields "" for a numeric or boolean
// field: two rows with different non-string categories collided on one key,
// and a row whose category is 0 keyed as "" and could match an unrelated
// row. Both failure directions end at a MISSED dedup, which is what
// resurrects a suggestion someone already reviewed.
func TestContentKeyCoercesLikePython(t *testing.T) {
	const src = `
import json, sys
d = json.loads(sys.argv[1])["row"]
print(json.dumps(list((str(d.get("category", "")), str(d.get("target", "")),
                       str(d.get("suggestion", "")).strip()))))
`
	rows := []map[string]any{
		{"category": "prompt_tweak", "target": "all", "suggestion": " padded "},
		{"category": 0, "target": 1.5, "suggestion": true},
		{"category": nil, "suggestion": "absent target"},
		// A trailing unit separator: str.strip() removes it, TrimSpace
		// does not, so the two spellings key this row differently and the
		// dedup misses.
		{"category": "c", "target": "t", "suggestion": "trailing sep\x1f"},
		{"category": "c", "target": "t", "suggestion": "trailing sep"},
	}
	keys := map[string]int{}
	for i, row := range rows {
		var want []string
		pyprobe.Probe{Stdlib: true}.RunJSON(t, src, &want,
			pyprobe.Arg(t, map[string]any{"row": row}))
		got := contentKeyOf(row)
		if wantKey := strings.Join(want, "\x00"); got != wantKey {
			t.Errorf("row %d: contentKey %q, CPython %q", i, got, wantKey)
		}
		keys[got]++
	}
	// Rows 3 and 4 differ only by a trailing U+001F, which str.strip()
	// removes — so CPython considers them the SAME finding and the port
	// must too. If this stops holding, the dedup has stopped working for
	// exactly the rows a torn write produces.
	if contentKeyOf(rows[3]) != contentKeyOf(rows[4]) {
		t.Error("a trailing U+001F made two identical findings key differently")
	}
}

// TestApplyRewritesTheStoreLikeCPython drives the last two rewrite sites:
// the keyed merge that replaces the applied row, and the constraint row
// APPENDED to a second shared store.
//
// The suggestion carries a `pattern`, so the new_guardrail branch runs a
// real apply rather than falling through to guidance-only — otherwise the
// appended row this test exists to compare never gets written, and the
// dynamic-constraints half of the comparison is two identical untouched
// files again.
//
// manual=true on both sides: the auto-apply gate is an explicit opt-in
// (config evolver.auto_apply / MARO_AUTO_APPLY_GUARDRAILS), and a test
// that flipped an env var would be measuring the gate instead of the
// rewrite.
func TestApplyRewritesTheStoreLikeCPython(t *testing.T) {
	runRewriteBoth(t, map[string]any{
		"verb": "apply", "id": "s5",
	}, func(ws string) {
		if _, err := Apply(ws, record.New(ws), nil, "s5", true); err != nil {
			t.Fatal(err)
		}
		// The precision maskStamps hides. Python's time.time() carries
		// microseconds; float64(Unix()) truncated to a whole second, so
		// every guardrail applied in the same second shared one added_at.
		// A whole-second value is possible by chance about one time in a
		// million, which is rare enough to assert and cheap enough to
		// re-run.
		raw, rerr := os.ReadFile(filepath.Join(ws, "memory", "dynamic-constraints.jsonl"))
		if rerr != nil {
			t.Fatal(rerr)
		}
		if !strings.Contains(string(raw), `"source": "s5"`) {
			t.Fatal("the guardrail row was never appended — this test is vacuous")
		}
		m := epochRe.FindString(string(raw))
		if !strings.Contains(m, ".") || strings.HasSuffix(m, ".0") {
			t.Errorf("added_at = %q — Python's time.time() is not whole seconds", m)
		}
	})
}
