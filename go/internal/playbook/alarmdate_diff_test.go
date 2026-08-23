package playbook

import (
	"regexp"
	"strings"
	"testing"
)

// alarmDate decides whether an alarm EXPIRES or is kept forever, and it is
// the one place in this package where three different Unicode digit rules
// meet: alarmRE matches Python's `\d` (760 code points), while CPython's
// strptime accepts a different set in each of the three date positions.
//
// The shipped fixtures all used ASCII stamps, so nothing discriminated.
// This sweeps every digit through every position against CPython itself.

// digitSweep asks CPython for the full `\d` set and returns it as runes.
func digitSweep(t *testing.T) []rune {
	t.Helper()
	var pts []int
	runPython(t, t.TempDir(),
		"import re;print(json.dumps([c for c in range(0x110000) "+
			"if re.fullmatch(r'\\d', chr(c))]))", &pts)
	if len(pts) < 700 {
		t.Fatalf("CPython reported only %d digits; the sweep would prove "+
			"little", len(pts))
	}
	out := make([]rune, 0, len(pts))
	for _, c := range pts {
		out = append(out, rune(c))
	}
	return out
}

// TestAlarmDateAgreesWithCPythonAtEveryDigitInEveryPosition puts each of
// the 760 digits into the year's last place, the month's last place, and
// the day's last place, and compares Go's verdict to CPython's strptime.
//
// One Python call for the whole sweep: ~2,300 candidates through
// subprocess-per-case would take minutes and the test would get deleted.
func TestAlarmDateAgreesWithCPythonAtEveryDigitInEveryPosition(t *testing.T) {
	digits := digitSweep(t)

	var stamps []string
	for _, r := range digits {
		d := string(r)
		stamps = append(stamps,
			"200"+d+"-01-01", // %Y: Unicode-aware
			"2001-0"+d+"-01", // %m: ASCII literals only
			"2001-01-1"+d,    // %d via [1-2]\d: Unicode-capable
			"2001-01-0"+d,    // %d via 0[1-9]: ASCII only
		)
	}
	// Positions the alternations treat specially, plus the year-zero case
	// CPython rejects after matching.
	stamps = append(stamps,
		"0000-01-01", "٠٠٠٠-01-01",
		"2001-01-30", "2001-01-31", "2001-01-32",
		"2001-13-01", "2001-00-01", "2001-01-00",
		"٢٠٠١-01-01",
		"٢٠٠١-01-1٢",
	)

	// CPython's answer for each: the parsed date, or "" for a ValueError.
	var want []string
	runPython(t, t.TempDir(),
		"from datetime import datetime\n"+
			"out=[]\n"+
			"for s in json.loads(sys.argv[1]):\n"+
			"    try: out.append(datetime.strptime(s,'%Y-%m-%d').strftime('%Y-%m-%d'))\n"+
			"    except ValueError: out.append('')\n"+
			"print(json.dumps(out))",
		&want, stamps)

	if len(want) != len(stamps) {
		t.Fatalf("CPython returned %d verdicts for %d stamps", len(want), len(stamps))
	}

	var mismatches []string
	parsed, refused := 0, 0
	for i, stamp := range stamps {
		line := "- x · alarm k @" + stamp
		got, ok := alarmDate(line)
		gotStr := ""
		if ok {
			gotStr = got.Format("2006-01-02")
		}
		if want[i] != "" {
			parsed++
		} else {
			refused++
		}
		if gotStr != want[i] {
			if len(mismatches) < 8 {
				mismatches = append(mismatches,
					strconvQuote(stamp)+": go="+strconvQuote(gotStr)+
						" py="+strconvQuote(want[i]))
			}
		}
	}
	if len(mismatches) > 0 {
		t.Fatalf("alarmDate disagrees with CPython on some stamps:\n  %s",
			strings.Join(mismatches, "\n  "))
	}

	// A sweep where CPython refused everything would pass against
	// `return false`, and one where it accepted everything would pass
	// against a parser that ignores its input.
	if parsed < 700 || refused < 700 {
		t.Fatalf("the sweep is one-sided: CPython parsed %d and refused %d",
			parsed, refused)
	}
	t.Logf("%d stamps: CPython parsed %d, refused %d", len(stamps), parsed, refused)
}

func strconvQuote(s string) string {
	return "\"" + strings.NewReplacer("\"", "\\\"").Replace(s) + "\""
}

// The sweep above pins the parse. This pins what the parse DECIDES: a
// non-ASCII-year alarm past its TTL must leave the document in both
// runtimes. Before r9 it left Python's and stayed in Go's forever, so the
// two runtimes fought over the same line.
func TestAMixedScriptAlarmExpiresInBothRuntimes(t *testing.T) {
	doc := "# P\n\n## Signals\n\n- disk full · alarm disk:full @٢٠٠١-01-01\n\n" +
		"## Cost\n\n- keep\n\n*Last updated: 2020-01-01*\n"

	pyWS := curateWorkspace(t, doc, 1<<30)
	var want pyCurateResult
	runPython(t, pyWS, pyCurateSnippet, &want, doc)

	goWS := curateWorkspace(t, doc, 1<<30)
	got := Curate(nil, goWS, nil, nil, true) //nolint:staticcheck // ctx unused on this path

	if want.Stats == nil {
		t.Fatal("CPython did not expire the alarm; this case proves nothing")
	}
	assertCurateAgrees(t, goWS, got, want)
}

// A control on the fixture above: the same document with an ASCII stamp
// behaves identically, so the mixed-script case is testing the SCRIPT and
// not something incidental about that document.
func TestTheSameAlarmInASCIIExpiresIdentically(t *testing.T) {
	doc := "# P\n\n## Signals\n\n- disk full · alarm disk:full @2001-01-01\n\n" +
		"## Cost\n\n- keep\n\n*Last updated: 2020-01-01*\n"

	pyWS := curateWorkspace(t, doc, 1<<30)
	var want pyCurateResult
	runPython(t, pyWS, pyCurateSnippet, &want, doc)

	goWS := curateWorkspace(t, doc, 1<<30)
	assertCurateAgrees(t, goWS, Curate(nil, goWS, nil, nil, true), want)
}

// The two strptime sub-patterns are transcribed from CPython's _strptime,
// so they must not drift from it. This re-derives them and fails if the
// interpreter on this box builds a different one.
func TestTheStrptimePatternsStillMatchCPythonsOwn(t *testing.T) {
	var got struct {
		Y string `json:"Y"`
		M string `json:"m"`
		D string `json:"d"`
	}
	runPython(t, t.TempDir(),
		"import _strptime;tr=_strptime.TimeRE();"+
			"print(json.dumps({'Y':tr['Y'],'m':tr['m'],'d':tr['d']}))", &got)

	for _, tc := range []struct{ name, want string }{
		{"Y", `(?P<Y>\d\d\d\d)`},
		{"m", `(?P<m>1[0-2]|0[1-9]|[1-9])`},
		{"d", `(?P<d>3[0-1]|[1-2]\d|0[1-9]|[1-9]| [1-9])`},
	} {
		have := map[string]string{"Y": got.Y, "m": got.M, "d": got.D}[tc.name]
		if have != tc.want {
			t.Errorf("CPython's %%%s pattern is now %q, not %q — alarmDate's "+
				"transcription and its comment are both stale",
				tc.name, have, tc.want)
		}
	}

	// And the Go transcriptions must accept exactly what those
	// alternations accept for a TWO-character group, which is all alarmRE
	// can produce.
	pyM := regexp.MustCompile(`^(?:` + strings.TrimSuffix(
		strings.TrimPrefix(got.M, `(?P<m>`), `)`) + `)$`)
	for _, s := range []string{"01", "09", "10", "12", "13", "00", "1٢"} {
		if strptimeMonth.MatchString(s) != (pyM.MatchString(s) && len(s) == 2) {
			t.Errorf("strptimeMonth disagrees with CPython's alternation on %q", s)
		}
	}
}
