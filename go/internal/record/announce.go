package record

import (
	"errors"
	"fmt"
	"os"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// SkipReport ports jsonl_utils.SkipReport: what a JSONL read threw away.
//
// Skipping a corrupt line is right — one bad append must not truncate the
// read of everything after it. Skipping it SILENTLY is not: the caller gets
// a shorter list and no way to tell data loss from an empty ledger. Under
// the retention decree the path is part of the result, and so is what fell
// out of it.
//
// Blank lines are not counted: they are not records. Missing is not loss —
// a store nothing has written to yet is legitimately empty, and reporting
// that as damage would train an operator to ignore the warning.
type SkipReport struct {
	Undecodable int  // bytes that are not UTF-8 (crash-torn append)
	Malformed   int  // not valid JSON
	NonDict     int  // valid JSON, but not a record
	Unreadable  bool // the file exists and could not be opened/read
	Missing     bool // no file — "no data yet", not loss
	Partial     bool // a bounded read that stopped early
}

// Dropped is SkipReport.dropped.
func (r SkipReport) Dropped() int { return r.Undecodable + r.Malformed + r.NonDict }

// Lost ports SkipReport.__bool__: truthy when something was LOST.
//
// Named Lost rather than given to a Bool() method because Python's truthiness
// is invisible at the call site (`if report:`) and Go's cannot be — and the
// one thing a reader of this predicate must not assume is that "there is a
// report" means "there was loss". A missing file produces a report and no
// loss, which is exactly the case the Python `__bool__` exists to exclude.
func (r SkipReport) Lost() bool { return r.Dropped() > 0 || r.Unreadable }

// Summary ports SkipReport.summary(), sentence for sentence — this string
// reaches an operator's log and the Python one is what they already know how
// to read.
func (r SkipReport) Summary() string {
	if r.Unreadable {
		return "unreadable (0 records returned — not an empty ledger)"
	}
	var bits []string
	for _, b := range []struct {
		n     int
		label string
	}{
		{r.Undecodable, "undecodable"},
		{r.Malformed, "malformed"},
		{r.NonDict, "non-dict"},
	} {
		if b.n != 0 {
			bits = append(bits, fmt.Sprintf("%d %s", b.n, b.label))
		}
	}
	scope := ""
	if r.Partial {
		scope = " in the scanned tail"
	}
	return fmt.Sprintf("dropped %d line(s)%s: ", r.Dropped(), scope) +
		strings.Join(bits, ", ")
}

// classifyLine ports jsonl_utils._classify: one line → a record, or nil with
// the reason counted.
//
// The caller has already split on "\n", so the terminator is gone; Python's
// forward path has not, hence the strip of a single trailing newline there
// and none here. Everything after that is the same ladder, in the same
// order, and the order is the classification: a line that is not UTF-8 is
// never asked whether it is JSON.
//
// The bucket for a clean line of the wrong shape is "non-dict" and NOT
// "malformed" — see ErrNotAnObject.
func classifyLine(line string) (map[string]any, string) {
	if IsFrameBlank(line) {
		return nil, "" // blank frame: not a record, not a loss
	}
	m, err := LoadsClean(line)
	switch {
	case err == nil:
		return m, ""
	case errors.Is(err, ErrNotAnObject):
		return nil, "non_dict"
	case errors.Is(err, ErrByteTainted):
		return nil, "undecodable"
	default:
		return nil, "malformed"
	}
}

// Announce is the warning text for a report, in Python's wording:
// `log.warning("%s: %s (%s)", what, report.summary(), path)`.
//
// Empty when nothing was LOST — a missing store is not damage, and a helper
// that announced it would train an operator to ignore the warning.
//
// Split out of ReadAllAnnounced because a caller that needs the buckets
// (ReadAllCounted) must not have to re-spell the format string to get the
// same sentence; two spellings of one operator-facing line is the prose
// divergence class this port keeps finding.
func (r SkipReport) Announce(what, path string) string {
	if !r.Lost() {
		return ""
	}
	return fmt.Sprintf("%s: %s (%s)", what, r.Summary(), path)
}

// ReadAllAnnounced ports jsonl_utils.read_jsonl_announced: every JSON object
// in a JSONL store, with any loss announced.
//
// One torn byte costs one record instead of the whole file. `what` names the
// loader in the warning so an operator can tell WHICH corpus is short, not
// just that something somewhere dropped lines.
//
// # Why this exists as a helper at all
//
// It is the fourth lens of the review arc — a helper you did not look for is
// a helper you will write again. Python funnels ~20 loaders through
// `read_jsonl_announced`; the port had spelled the read out inline at every
// site, and every one of those copies had quietly dropped the announcement,
// because the announcement lives in the helper and nothing else. The
// differential that caught it is `TestLoadSkillTestsMatchesCPython`: CPython
// said `dropped 1 line(s): 1 malformed` and the port said nothing at all.
//
// Returns the rows and the warning text — empty when there was no loss. The
// port hands warnings back rather than logging them from inside because the
// caller is what knows whether it has a logger; the Python module logs
// directly, and matching that would mean a package-level logger in the
// record package that tests then have to capture.
func ReadAllAnnounced(path, what string) ([]map[string]any, string) {
	rows, report := ReadAllCounted(path)
	return rows, report.Announce(what, path)
}

// ReadAllCounted is read_jsonl_tail_counted with limit=None: same records,
// same order, same never-raises contract, plus the report.
//
// The bounded (`limit=N`) path is deliberately NOT ported here. Python reads
// backwards in chunks for it and sets Partial so the counts announce
// themselves as a lower bound; a port that quietly full-scanned and called
// the counts whole-file would be telling the same lie one level up. When a
// caller needs a tail, that path gets written and probed on its own.
//
// Exported because Missing and Unreadable are not distinguishable from the
// announced STRING, and a loader whose result type carries that distinction
// (skills.LoadResult.Unreadable) would otherwise have to sniff the prose to
// recover it.
func ReadAllCounted(path string) ([]map[string]any, SkipReport) {
	raw, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, SkipReport{Missing: true}
		}
		// Python reaches this through `path.exists()` returning True and
		// `path.open` raising OSError.
		//
		// NAMED DIVERGENCE, and it is louder than "which flavour of
		// nothing". For a path whose PARENT directory is unsearchable,
		// `Path.exists()` swallows the PermissionError and returns False
		// (measured), so Python builds SkipReport(missing=True), its
		// __bool__ is false, and NOTHING IS LOGGED. Go gets EACCES from
		// ReadFile, lands in Unreadable, and announces
		// "unreadable (0 records returned — not an empty ledger)".
		//
		// One runtime announces a loss and the other is silent about the
		// same directory. Both refuse to invent records, which is the half
		// that matters for the store; the half that matters for an
		// operator is that only one of them will say why the corpus went
		// empty. The port is on the louder side deliberately.
		return nil, SkipReport{Unreadable: true}
	}
	var out []map[string]any
	rep := SkipReport{}
	for _, line := range strings.Split(string(raw), "\n") {
		m, bucket := classifyLine(line)
		switch bucket {
		case "":
			if m != nil {
				out = append(out, m)
			}
		case "undecodable":
			rep.Undecodable++
		case "malformed":
			rep.Malformed++
		case "non_dict":
			rep.NonDict++
		}
	}
	return out, rep
}

// ReadAllAnnouncedOrdered is ReadAllAnnounced handing back rows that still
// know their key ORDER.
//
// Same reader, same buckets, same warning — the only difference is the row
// type. It exists because `map[string]any` silently discards a fact the
// store depends on, and the discard is invisible until something re-emits
// the row:
//
// A Python loader gets an insertion-ordered dict. Mutating a key leaves it
// where it was, a new key lands at the tail, and `json.dumps` writes the
// row back in the order the FILE had. The port read into a Go map, whose
// key order does not exist, and re-emitted through `pyval.FromPlain`, whose
// doc names the sorted output as a LOSS. So every read-modify-write
// callback rewrote every key of every row it touched into alphabetical
// order — in a store the Python runtime reads, appends to, and diffs.
//
// Nothing about JSON semantics changes, which is exactly why no test caught
// it for so long: both runtimes parse either byte sequence to the same
// mapping. What changes is the BYTES, and the bytes are the shared artifact
// — a ledger diff shows every touched row as fully rewritten, a content
// hash over the store disagrees across runtimes, and a dedup keyed on the
// serialized line stops matching.
//
// Use this whenever a row read from a store may be written back. Use
// ReadAllAnnounced when the rows are only ever inspected.
func ReadAllAnnouncedOrdered(path, what string) ([]pyval.Obj, string) {
	rows, report := ReadAllCountedOrdered(path)
	return rows, report.Announce(what, path)
}

// ReadAllCountedOrdered is ReadAllCounted with ordered rows.
//
// The bucketing MUST stay identical to ReadAllCounted's — a line the two
// readers classify differently would make a caller's CHOICE OF ROW TYPE
// change which records it sees, which is not a thing a caller should be
// able to do by accident.
//
// It is not enforced by construction, and that is deliberate rather than
// lazy: LoadsClean and LoadsCleanOrdered reach their verdict through
// different decoders (a bare json.Decoder against pyval.LoadsOrdered), so
// making one a projection of the other would silently swap the number
// types the plain reader hands its ~20 existing callers. Measured instead,
// and pinned: TestOrderedReaderClassifiesLikeThePlainOne walks the same
// line corpus through both. If that test ever fails, the two readers have
// stopped being the same reader and this doc comment is the promise that
// broke.
func ReadAllCountedOrdered(path string) ([]pyval.Obj, SkipReport) {
	raw, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, SkipReport{Missing: true}
		}
		return nil, SkipReport{Unreadable: true}
	}
	var out []pyval.Obj
	rep := SkipReport{}
	for _, line := range strings.Split(string(raw), "\n") {
		o, bucket := classifyLineOrdered(line)
		switch bucket {
		case "":
			if o != nil {
				out = append(out, o)
			}
		case "undecodable":
			rep.Undecodable++
		case "malformed":
			rep.Malformed++
		case "non_dict":
			rep.NonDict++
		}
	}
	return out, rep
}

// classifyLineOrdered is classifyLine's real body: same LoadsClean rules,
// same three loss buckets, ordered result.
func classifyLineOrdered(line string) (pyval.Obj, string) {
	if IsFrameBlank(line) {
		return nil, "" // blank frame: not a record, not a loss
	}
	o, err := LoadsCleanOrdered(line)
	switch {
	case err == nil:
		return o, ""
	case errors.Is(err, ErrNotAnObject):
		return nil, "non_dict"
	case errors.Is(err, ErrByteTainted):
		return nil, "undecodable"
	default:
		return nil, "malformed"
	}
}
