// Package dispatch ports dispatch_envelope.py: the typed dispatch envelope
// a dispatching agent wraps a user's verbatim ask in, and the two attachment
// lanes that ride beside it.
//
// The separation the module exists for is a TRUST boundary, and it is why
// there are two storage functions rather than one parameterised one:
//
//   - StoreAttachments takes TEXT a dispatcher supplied. A dispatcher's
//     payload is untrusted machine input, so this lane can write strings and
//     nothing else.
//   - StoreOperatorAttachments copies LOCAL FILES a person named. Different
//     provenance, and it must support bytes (the case that opened it was a
//     screenshot of a paper).
//
// Widening the first to binary would hand every dispatcher a binary write
// primitive. The Python keeps them apart on purpose; so does this.
//
// Deliberately unported, NAMED: nothing. Every public function in
// dispatch_envelope.py is here.
package dispatch

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Version is dispatch_envelope.ENVELOPE_VERSION.
const Version = "maro-dispatch/v1"

// MaxAttachmentBytes is _MAX_ATTACHMENT_BYTES.
const MaxAttachmentBytes = 32 * 1024 * 1024

// Error is EnvelopeError: a payload declared the envelope version and then
// violated its shape. Python subclasses ValueError; what matters to callers
// is that it is a NAMED refusal they catch and print, distinct from the
// RuntimeError expandUser can raise beside it.
type Error struct{ Msg string }

func (e *Error) Error() string { return e.Msg }

func errorf(format string, a ...any) *Error {
	return &Error{Msg: fmt.Sprintf(format, a...)}
}

// Envelope is the frozen DispatchEnvelope dataclass.
//
// AttachedArtifacts stays as decoded objects rather than a struct because
// the sidecar writes `art.get("name")` RAW — the value straight out of the
// payload, not a re-spelling of it — and because a dispatcher may attach
// keys this port does not model.
type Envelope struct {
	UserAsk             string
	OperatorContext     string
	OperatorConstraints []string
	AttachedArtifacts   []pyval.Obj
}

// Stored is one row of StoreAttachments' return: exactly the three keys
// OperatorBlock reads.
type Stored struct {
	Name   string
	Path   string
	Source string
}

// OperatorStored is one row of StoreOperatorAttachments' return. It carries
// two fields Stored does not — the digest and the byte count — because
// OperatorAttachmentBlock prints both, and it is a separate type for the
// same reason the two storage functions are separate.
type OperatorStored struct {
	Name   string
	Path   string
	Source string
	SHA256 string
	Bytes  int
}

// ParseDispatchPayload parses a dispatch payload. A nil envelope with a nil
// error means untyped prose — the fallback lane, which is most traffic.
//
// It refuses ONLY when the payload opts into the contract and then breaks
// it. Prose, non-JSON, and JSON without the version key are all legacy prose
// dispatches and come back untouched.
//
// The argument is `any` rather than `string` because Python's first line is
// an isinstance check: a caller handing this a dict or None gets None back,
// not a crash, and one of the callers reads it straight out of a task row.
func ParseDispatchPayload(payload any) (*Envelope, error) {
	s, ok := payload.(string)
	if !ok {
		return nil, nil
	}
	text := pytext.Strip(s)
	if !strings.HasPrefix(text, "{") {
		return nil, nil
	}
	raw, jerr := pyval.LoadsOrdered(text)
	if jerr != nil {
		// A `{`-leading payload that NAMES the contract version and then
		// fails to parse is almost certainly a truncated typed dispatch.
		// Running it as prose would execute a mangled goal, so this is the
		// one place a parse failure is louder than a fallback.
		if strings.Contains(text, Version) {
			return nil, errorf("payload mentions %s but is not valid JSON "+
				"(truncated or corrupted dispatch?)", Version)
		}
		return nil, nil
	}
	data, ok := raw.(pyval.Obj)
	if !ok {
		return nil, nil
	}
	if v, _ := data.Get("envelope"); pyval.Plain(v) != Version {
		return nil, nil
	}

	askRaw, _ := data.Get("user_ask")
	ask, isStr := askRaw.(string)
	if !isStr || pytext.Strip(ask) == "" {
		return nil, errorf("envelope declares %s but user_ask is missing or empty",
			Version)
	}
	var context string
	if v, present := data.Get("operator_context"); present {
		c, isStr := v.(string)
		if !isStr {
			return nil, errorf("operator_context must be a string")
		}
		context = c
	}
	var constraints []string
	if v, present := data.Get("operator_constraints"); present {
		lst, isList := v.(pyval.List)
		if !isList {
			return nil, errorf("operator_constraints must be a list of strings")
		}
		for _, item := range lst {
			c, isStr := item.(string)
			if !isStr {
				return nil, errorf("operator_constraints must be a list of strings")
			}
			// The empty-after-strip entries are dropped, not kept as blanks:
			// a constraints block with a bare "- " line reads as a rule
			// nobody wrote down.
			if stripped := pytext.Strip(c); stripped != "" {
				constraints = append(constraints, stripped)
			}
		}
	}
	var artifacts []pyval.Obj
	if v, present := data.Get("attached_artifacts"); present {
		lst, isList := v.(pyval.List)
		if !isList {
			return nil, errorf("attached_artifacts must be a list of objects")
		}
		for _, item := range lst {
			art, isObj := item.(pyval.Obj)
			if !isObj {
				return nil, errorf("attached_artifacts must be a list of objects")
			}
			artifacts = append(artifacts, art)
		}
	}
	// The per-artifact checks run as a SECOND pass in Python, after the
	// whole list has been type-checked. The order is visible: a list holding
	// one non-object and one nameless object reports the list error, never
	// the name error.
	for _, art := range artifacts {
		nameRaw, _ := art.Get("name")
		name, isStr := nameRaw.(string)
		if !isStr || pytext.Strip(name) == "" {
			return nil, errorf("attached artifact missing a non-empty name")
		}
		if c, _ := art.Get("content"); !isString(c) {
			return nil, errorf("attached artifact %s missing string content",
				pyval.Repr(name))
		}
	}

	return &Envelope{
		UserAsk:             pytext.Strip(ask),
		OperatorContext:     pytext.Strip(context),
		OperatorConstraints: constraints,
		AttachedArtifacts:   artifacts,
	}, nil
}

func isString(v any) bool { _, ok := v.(string); return ok }

// getOrEmpty is `str(d.get(key, ""))`: the default applies to a MISSING key
// only, so a key present and null spells the literal "None" — which is what
// str() does to it and what the filesystem then holds.
func getOrEmpty(o pyval.Obj, key string) string {
	v, present := o.Get(key)
	if !present {
		return ""
	}
	return pyval.Str(v)
}

var nonNameRe = regexp.MustCompile(`[^A-Za-z0-9._-]`)

// safeName is _safe_name: an artifact name is a LABEL, never a path, so
// traversal is impossible by construction rather than by validation.
//
// The order is load-bearing and each step drops something the next relies
// on: basename first (so `x/../y` is `y`), then the substitution (so every
// surviving rune is ASCII), then the strip of leading/trailing `.` and `_`
// (so `..` and `.gitignore` cannot address or hide), then the fallback, then
// the 120 cap. The cap is a BYTE slice here and a codepoint slice in Python,
// which agree only because the substitution above it guarantees pure ASCII.
func safeName(name string) string {
	base := pypath.Name(strings.ReplaceAll(name, "\\", "/"))
	base = strings.Trim(nonNameRe.ReplaceAllString(base, "_"), "._")
	if base == "" {
		base = "artifact"
	}
	if len(base) > 120 {
		base = base[:120]
	}
	return base
}

// safeKey is _safe_key: a key is a directory NAME, never a path fragment.
//
// The substitution alone left `..` intact, so a key of `..` addressed the
// parent directory — safeName had always collapsed that case and this had
// not. Dot-only results are REPLACED rather than stripped, because stripping
// would silently map several distinct keys onto one directory.
func safeKey(key any) string {
	cleaned := nonNameRe.ReplaceAllString(pyval.Str(key), "_")
	if cleaned == "" || strings.Trim(cleaned, ".") == "" {
		return "dispatch"
	}
	return cleaned
}

func outputDir(ws string) string { return filepath.Join(ws, "output") }

// StoreAttachments writes attached artifacts under
// output/dispatch-artifacts/<key>/.
//
// Each gets a provenance sidecar carrying the dispatcher-CLAIMED source plus
// the sha256 and byte count of what actually landed — the claim and the
// measurement side by side, because only one of them is evidence. Returns
// the rows OperatorBlock names.
//
// It returns an error on I/O failure rather than degrading: a dispatch that
// promised reference material must not run silently without it. That is the
// opposite posture from the landing functions below, and the difference is
// deliberate — see LandInRunDir.
func StoreAttachments(ws string, env *Envelope, key any, now time.Time) ([]Stored, error) {
	if env == nil || len(env.AttachedArtifacts) == 0 {
		return nil, nil
	}
	dest := filepath.Join(outputDir(ws), "dispatch-artifacts", safeKey(key))
	if err := os.MkdirAll(dest, record.NewDirMode); err != nil {
		return nil, err
	}
	var stored []Stored
	taken := map[string]bool{}
	for _, art := range env.AttachedArtifacts {
		nameRaw, namePresent := art.Get("name")
		base := safeName(getOrEmpty(art, "name"))
		candidate, i := base, 1
		for taken[candidate] {
			candidate = fmt.Sprintf("%d-%s", i, base)
			i++
		}
		taken[candidate] = true
		path := filepath.Join(dest, candidate)
		content := getOrEmpty(art, "content")
		if err := os.WriteFile(path, []byte(content), 0o666); err != nil {
			return nil, err
		}
		sourceRaw, _ := art.Get("source")
		source := pyval.StrOrEmpty(sourceRaw)
		sum := sha256.Sum256([]byte(content))
		// `name` here is the RAW value, not safeName's output and not
		// str()'d: the sidecar records what the dispatcher CLAIMED the file
		// was called, while the filename beside it records what was safe to
		// write. Collapsing the two would erase the only record that they
		// ever differed.
		sidecar := pyval.Obj{
			{Key: "name", Val: pyval.FromPlain(nameRaw)},
			{Key: "source", Val: source},
			{Key: "sha256", Val: hex.EncodeToString(sum[:])},
			{Key: "bytes", Val: len(content)},
			{Key: "stored_at", Val: pyval.NowISO(now)},
			{Key: "dispatch_key", Val: pyval.Str(key)},
		}
		text, err := pyval.DumpsIndentN(sidecar, 1)
		if err != nil {
			return nil, err
		}
		if err := os.WriteFile(path+".provenance.json", []byte(text), 0o666); err != nil {
			return nil, err
		}
		// A THIRD spelling of the same field, and all three are in the
		// Python: the filename took `str(art.get("name", ""))`, the sidecar
		// takes the value RAW, and this row takes `str(art.get("name"))`
		// with NO default — so an artifact carrying an explicit null name
		// is stored as `artifact`, recorded as `null`, and reported as the
		// literal string "None". A port that picked one spelling for all
		// three would be wrong twice.
		reported := "None"
		if namePresent {
			reported = pyval.Str(nameRaw)
		}
		stored = append(stored, Stored{
			Name:   reported,
			Path:   path,
			Source: source,
		})
	}
	return stored, nil
}

// StoreOperatorAttachments copies operator-chosen local files under
// output/operator-attachments/<key>/.
//
// Same provenance contract as StoreAttachments, but the source is a local
// path an operator named rather than content a dispatcher supplied. It
// refuses a named file it cannot read: an operator who attached a file and
// got a run without it has been silently ignored, which is worse than a
// refusal.
func StoreOperatorAttachments(ws string, paths []string, key any) ([]OperatorStored, error) {
	if len(paths) == 0 {
		return nil, nil
	}
	dest := filepath.Join(outputDir(ws), "operator-attachments", safeKey(key))
	if err := os.MkdirAll(dest, record.NewDirMode); err != nil {
		return nil, err
	}
	var out []OperatorStored
	for _, raw := range paths {
		src, err := pypath.ExpandUser(pypath.Str(raw))
		if err != nil {
			return nil, err
		}
		// A symlink hides WHAT is being attached: the name says one thing
		// and the bytes come from wherever it points — and those bytes land
		// in an area that is bind-mounted into a container and read by a
		// model. Refuse rather than follow, and name the target so an
		// operator who meant it can pass the real path.
		if fi, lerr := os.Lstat(src); lerr == nil && fi.Mode()&os.ModeSymlink != 0 {
			target := "<unresolvable>"
			if r, ok := pypath.Realpath(src); ok {
				target = r
			}
			return nil, errorf("attachment is a symlink: %s -> %s. Pass the "+
				"resolved path directly if that is what you meant to attach.",
				src, target)
		}
		st, serr := os.Stat(src)
		if serr != nil || !st.Mode().IsRegular() {
			return nil, errorf("attachment not found or not a file: %s", src)
		}
		if size := st.Size(); size > MaxAttachmentBytes {
			return nil, errorf("attachment %s is %d bytes, over the "+
				"%d-byte limit", pypath.Name(src), size, MaxAttachmentBytes)
		}
		data, rerr := os.ReadFile(src)
		if rerr != nil {
			return nil, errorf("attachment unreadable: %s: %s", src, oserr(rerr))
		}
		base := safeName(pypath.Name(src))
		target := filepath.Join(dest, base)
		for n := 1; ; {
			existing, eerr := os.ReadFile(target)
			if eerr != nil || bytesEqual(existing, data) {
				break
			}
			n++
			target = filepath.Join(dest,
				fmt.Sprintf("%s-%d%s", pypath.Stem(base), n, pypath.Suffix(base)))
		}
		// A write failure here (disk full, permission denied) must surface
		// as the lane's own refusal, not a raw traceback — the CLI catches
		// this type and prints an actionable line, while a traceback on
		// `--attach` reads as "broken" rather than "fix your disk".
		if werr := os.WriteFile(target, data, 0o666); werr != nil {
			return nil, errorf("could not store attachment %s: %s",
				pypath.Name(src), oserr(werr))
		}
		sum := sha256.Sum256(data)
		rec := OperatorStored{
			Name:   base,
			Path:   target,
			Source: "operator:" + src,
			SHA256: hex.EncodeToString(sum[:]),
			Bytes:  len(data),
		}
		sidecar := pyval.Obj{
			{Key: "name", Val: rec.Name},
			{Key: "path", Val: rec.Path},
			{Key: "source", Val: rec.Source},
			{Key: "sha256", Val: rec.SHA256},
			{Key: "bytes", Val: rec.Bytes},
			{Key: "lane", Val: "operator"},
		}
		text, derr := pyval.DumpsIndentN(sidecar, 2)
		if derr != nil {
			return nil, derr
		}
		if werr := os.WriteFile(target+".provenance.json", []byte(text), 0o666); werr != nil {
			return nil, werr
		}
		out = append(out, rec)
	}
	return out, nil
}

// LandOperatorAttachments copies stored operator attachments into
// <run_dir>/fetch-raw/operator/.
//
// Why this exists rather than an absolute path in the goal: the container
// executor HARD-EXCLUDES the workspace root from its mount map, so a file
// under output/ is unreadable from inside a containerized worker. The run
// dir is the run's cwd and is mounted rw — landing here is what makes an
// attachment reachable by the step that must read it.
func LandOperatorAttachments(ws, runDir string, key any) int {
	return land(ws, runDir, "operator-attachments", key, "operator")
}

func land(ws, runDir, area string, key any, sub string) int {
	src := filepath.Join(outputDir(ws), area, safeKey(key))
	if fi, err := os.Stat(src); err != nil || !fi.IsDir() {
		return 0
	}
	dest := filepath.Join(runDir, "fetch-raw", sub)
	if err := os.MkdirAll(dest, record.NewDirMode); err != nil {
		return 0
	}
	names, err := sortedFileNames(src)
	if err != nil {
		return 0
	}
	copied := 0
	for _, name := range names {
		data, err := os.ReadFile(filepath.Join(src, name))
		if err != nil {
			// Python's blanket except abandons the whole landing and
			// reports ZERO, even though earlier files did land. The count
			// is what the caller logs, so under-reporting here is the
			// honest half of a partial copy.
			return 0
		}
		target := filepath.Join(dest, name)
		if existing, err := os.ReadFile(target); err == nil {
			// Idempotent re-land: identical bytes are a no-op. DIFFERING
			// bytes were also skipped once, which silently dropped one of
			// two distinct files — while the storage sibling disambiguates
			// in exactly this case. Keep both; a run tree quietly holding
			// one of two artifacts is worse than one holding an oddly-named
			// pair.
			if bytesEqual(existing, data) {
				continue
			}
			stem, suffix := pypath.Stem(name), pypath.Suffix(name)
			n := 2
			for {
				cur, err := os.ReadFile(target)
				if err != nil || bytesEqual(cur, data) {
					break
				}
				target = filepath.Join(dest, fmt.Sprintf("%s-%d%s", stem, n, suffix))
				n++
			}
			if _, err := os.Stat(target); err == nil {
				continue
			}
		}
		if err := os.WriteFile(target, data, 0o666); err != nil {
			return 0
		}
		copied++
	}
	return copied
}

// LandInRunDir copies this dispatch's stored attachments into
// <run_dir>/fetch-raw/dispatch/, provenance sidecars included.
//
// The output/dispatch-artifacts/<job_id>/ copy is the dispatch-side record;
// runs are self-contained artifact trees, and the container executor
// hard-excludes the workspace root from its mount map, so the run-dir copy
// is the one that travels with the run.
//
// Fail-SOFT by contract, unlike StoreAttachments: the operator block already
// references the dispatch-side paths, so on the subprocess lane a copy
// failure degrades self-containment rather than the run. Idempotent in the
// blunt sense — an existing file is left alone whatever its bytes, which is
// where it differs from its operator-lane sibling.
func LandInRunDir(ws, runDir string, jobID any) int {
	src := filepath.Join(outputDir(ws), "dispatch-artifacts", safeKey(jobID))
	if fi, err := os.Stat(src); err != nil || !fi.IsDir() {
		return 0
	}
	dest := filepath.Join(runDir, "fetch-raw", "dispatch")
	if err := os.MkdirAll(dest, record.NewDirMode); err != nil {
		return 0
	}
	names, err := sortedFileNames(src)
	if err != nil {
		return 0
	}
	copied := 0
	for _, name := range names {
		target := filepath.Join(dest, name)
		if _, err := os.Stat(target); err == nil {
			continue
		}
		data, err := os.ReadFile(filepath.Join(src, name))
		if err != nil {
			return 0
		}
		if err := os.WriteFile(target, data, 0o666); err != nil {
			return 0
		}
		copied++
	}
	return copied
}

// sortedFileNames is `sorted(src.iterdir())` filtered by is_file().
//
// Python sorts PATH OBJECTS, which on posix compare as their string form;
// within one directory that is the same order as sorting the names, and the
// names sort by codepoint there and by UTF-8 byte here — orders that agree,
// because UTF-8 preserves codepoint order.
func sortedFileNames(dir string) ([]string, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var names []string
	for _, e := range entries {
		// is_file() FOLLOWS symlinks, so a link to a regular file counts.
		fi, err := os.Stat(filepath.Join(dir, e.Name()))
		if err != nil || !fi.Mode().IsRegular() {
			continue
		}
		names = append(names, e.Name())
	}
	sort.Strings(names)
	return names, nil
}

func bytesEqual(a, b []byte) bool {
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

// oserr renders a Go file error the way str(OSError) reads in the messages
// above: `[Errno N] text: 'path'` is Python's spelling, and Go's is
// `open path: text`. The two differ and this port does not pretend
// otherwise — NAMED residual, and it is confined to two operator-facing
// strings that no store ever holds.
func oserr(err error) string { return err.Error() }

// OperatorAttachmentBlock is the advisory block naming landed attachments
// for the run prompt.
//
// It names the STORED ABSOLUTE path, because that is the one bind-mounted
// (read-only, scoped to this run's key) and therefore the one that exists
// from inside the container. A run-dir-relative path was tried first and
// failed: for a project-scoped run the cwd is the project dir, so
// `fetch-raw/operator/...` resolved to nothing and five separate filesystem
// searches came back empty.
func OperatorAttachmentBlock(stored []OperatorStored) string {
	if len(stored) == 0 {
		return ""
	}
	lines := []string{"Operator-attached files (supplied by the person who set this " +
		"goal, NOT retrieved by this run):"}
	for _, rec := range stored {
		lines = append(lines, fmt.Sprintf("  - %s (from %s, %d bytes, sha256 %s…)",
			rec.Path, rec.Source, rec.Bytes, headRunes(rec.SHA256, 12)))
	}
	lines = append(lines,
		"Read them from that path when a claim depends on their contents. "+
			"Anything you take from one is operator-supplied evidence: cite it "+
			"as the attachment, never as a source you retrieved, and say so if "+
			"it is the only support for a claim.")
	return strings.Join(lines, "\n")
}

// OperatorBlock renders operator fields as one labeled advisory block.
//
// Empty means "no operator channel" — callers treat it that way. The
// authority level is stated IN BAND because the run prompt is the only place
// the model sees it: advisory framing, not part of the user's ask.
func OperatorBlock(env *Envelope, stored []Stored) string {
	if env == nil {
		return ""
	}
	var parts []string
	if env.OperatorContext != "" {
		parts = append(parts, env.OperatorContext)
	}
	if len(env.OperatorConstraints) > 0 {
		var b strings.Builder
		b.WriteString("Operator constraints (apply to THIS run only):\n")
		for i, c := range env.OperatorConstraints {
			if i > 0 {
				b.WriteByte('\n')
			}
			b.WriteString("- " + c)
		}
		parts = append(parts, b.String())
	}
	if len(stored) > 0 {
		var lines []string
		for _, rec := range stored {
			src := ""
			if rec.Source != "" {
				src = fmt.Sprintf(" (source: %s)", rec.Source)
			}
			lines = append(lines, fmt.Sprintf("- %s → %s%s", rec.Name, rec.Path, src))
		}
		parts = append(parts, "Attached reference artifacts on disk:\n"+
			strings.Join(lines, "\n"))
	}
	if len(parts) == 0 {
		return ""
	}
	return "== Operator dispatch context (advisory — authored by the dispatching " +
		"agent, NOT part of the user's ask) ==\n" +
		strings.Join(parts, "\n\n") +
		"\n== End operator context =="
}

// headRunes is Python's `s[:n]` on a str: n CODEPOINTS, not n bytes. A
// sha256 hexdigest is ASCII so the two agree here, and the function is
// written codepoint-wise anyway because the next caller's string may not be.
func headRunes(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}
