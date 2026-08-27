// Export — gather Class C + Class A artifacts from a workspace into an
// UNSEALED pack, scrubbed, with a REVIEW.md a human actually reads before
// sealing. Ports pack.export_pack.
//
// Honesty framing (preserve verbatim in any UI copy — design doc §4): the
// sharing guarantee is mechanical scrub for secret-shaped strings +
// mechanical redaction of known local identifiers + a mandatory human
// review gate. We do not claim mechanical anonymization. A pack is a
// letter — you proofread letters.
package pack

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/scrub"
)

// ExportOpts mirrors export_pack's keyword surface. Home/Hostname/
// Denylist non-nil pins are for deterministic tests; real exports derive
// them from this machine.
type ExportOpts struct {
	Name             string
	Label            string
	Workspace        string // resolved by the caller (live-store discipline)
	OutDir           string // default <ws>/output/packs
	IncludeMedium    bool
	IncludeKnowledge bool
	IncludePlaybook  bool
	Denylist         []string // nil = derive via DefaultDenylist
	Home             string   // "" = this machine's
	Hostname         string   // "" = this machine's
}

// ExportResult reports what shipped.
type ExportResult struct {
	PackPath   string
	ReviewPath string
	Manifest   map[string]any
}

// DefaultDenylist ports pack.default_denylist: email-shaped identity from
// environment + git config + the pack.export_denylist config key. Never
// hardcode identifiers here (§4) — this is the one seam that gathers them.
func DefaultDenylist() []string {
	items := map[string]bool{}
	for _, key := range []string{"EMAIL", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"} {
		if v := os.Getenv(key); v != "" {
			items[v] = true
		}
	}
	if out, err := exec.Command("git", "config", "--get", "user.email").Output(); err == nil {
		// `.strip()`, not TrimSpace. Nothing realistic reaches the
		// difference — git prints one email and a newline — but this is the
		// same sweep class as the five sites above it, and a site skipped by
		// name is a claim nobody re-examines (lens 6).
		if v := pytext.Strip(string(out)); v != "" {
			items[v] = true
		}
	}
	cfg, _ := config.Load()
	if extra, ok := cfg["pack"].(map[string]any); ok {
		if lst, ok := extra["export_denylist"].([]any); ok {
			// `items.update(str(x) for x in extra if x)` — a TRUTHINESS
			// filter and a str() COERCION, not a type assertion. A
			// config entry of 42 or true is a denylist entry CPython
			// redacts and the port did not, and the direction that
			// divergence fails in is the leak direction: the port ships
			// a value the operator asked to have scrubbed.
			//
			// Sixth instance of "a Python idiom spelled as a Go type
			// assertion" in this file's review history.
			for _, x := range lst {
				if pyval.Truthy(x) {
					items[asString(x)] = true
				}
			}
		}
	}
	sorted := make([]string, 0, len(items))
	for k := range items {
		sorted = append(sorted, k)
	}
	sort.Strings(sorted)
	return sorted
}

var redactionMarkers = []string{"[REDACTED]", "[HOME]", "[USER]", "[HOST]"}

func reviewSection(cls, relPath, content string) string {
	var flagged []string
	// `content.splitlines()`, not a split on "\n" — the sibling of the strip
	// on the render line below, and it was left behind when that one was
	// fixed (lens 3: a fix is evidence about its SIBLINGS).
	//
	// A marker with an interior \x0b or \x1c is one flagged line to Python
	// and one longer line here, and the strip cannot mask it because the
	// separator is not at either end. REVIEW.md is hashed into the seal, so
	// the two runtimes seal the same workspace to different digests. It also
	// changes the COUNT when a marker lands in each fragment.
	for _, ln := range pytext.SplitLines(content) {
		for _, m := range redactionMarkers {
			if strings.Contains(ln, m) {
				flagged = append(flagged, ln)
				break
			}
		}
	}
	section := fmt.Sprintf("## %s  (class: %s)\n\n```\n%s\n```\n",
		relPath, cls, strings.TrimRight(content, "\n"))
	if len(flagged) > 0 {
		section += "\n**Redacted lines:**\n"
		for _, ln := range flagged {
			// `ln.strip()`, not TrimSpace: this line is rendered into
			// REVIEW.md and REVIEW.md is hashed into the seal
			// (review_manifest_sha256), so a flagged line ending in an
			// information separator seals to a different digest in the two
			// runtimes.
			section += fmt.Sprintf("- `%s`\n", pytext.Strip(ln))
		}
	}
	return section
}

func buildReviewMD(manifest map[string]any, sections []string) string {
	origin, _ := manifest["origin"].(map[string]any)
	header := fmt.Sprintf(
		"# Review — %s\n\nCreated: %s\nLabel: %s\n\n"+
			"This is a mechanical scrub of secret-shaped strings and known local "+
			"identifiers, not anonymization. We do not claim mechanical "+
			"anonymization. A pack is a letter — proofread it before sealing. "+
			"Everything below is the artifact's real content exactly as it will "+
			"ship; lines flagged \"Redacted lines\" were changed by the "+
			"scrubber.\n\n---\n\n",
		manifest["name"], manifest["created_at"], origin["label"])
	if len(sections) == 0 {
		return header + "*(no artifacts in this pack)*\n"
	}
	return header + strings.Join(sections, "\n---\n\n") + "\n"
}

// nowISO is `datetime.now(timezone.utc).isoformat()`, which is
// pyval.NowISO — not a local format string.
//
// The local copy this replaced spelled the layout by hand as
// "2006-01-02T15:04:05.000000-07:00" — offset and width right, but
// ".000000" for a whole second, where isoformat omits the fraction
// entirely. One call in a million lands there, which is exactly the kind
// of divergence a hand-written layout survives for years.
//
// It was one of FOUR byte-identical copies of that layout (graduation,
// pack/export, scans, skills/stats), which is why the answer was a census
// and not four fixes: a helper you did not look for is a helper you will
// write again, and the defect was the count. The line that used to stand
// here said "the FIFTH copy" — in all four files, and in inspector, which
// had no copy at all. Five claims to be the fifth is a paste, not a
// measurement (adversarial r2, L4).
func nowISO() string { return pyval.NowISO(time.Now().UTC()) }

// Export gathers, scrubs, and writes an unsealed pack + REVIEW.md
// companion. Artifact classes and paths match Python's exporter so either
// runtime can import the result.
func Export(opts ExportOpts) (*ExportResult, error) {
	ws := opts.Workspace
	if ws == "" {
		return nil, fmt.Errorf("export: workspace not resolved by caller")
	}
	out := opts.OutDir
	if out == "" {
		out = filepath.Join(ws, "output", "packs")
	}
	denylist := opts.Denylist
	if denylist == nil {
		denylist = DefaultDenylist()
	}
	ids := scrub.BuildIdentifiers(opts.Home, opts.Hostname, denylist)
	scrubText := func(s string) string { return ids.Apply(scrub.Secrets(s)) }

	// `json.dumps(scrub_identifiers(scrub(obj), ...))` — a BARE json.dumps,
	// which is not the canonical spelling and is not close to it.
	//
	// The first cut re-emitted these rows with CanonicalJSON, the encoder
	// this package uses for the hashed pack.json metadata: sorted keys,
	// `,`/`:` separators, ensure_ascii=False. A bare json.dumps is the
	// opposite on all three counts — insertion order, `", "`/`": "`, and
	// non-ASCII escaped to \uXXXX. Every scrubbed row therefore shipped as
	// different BYTES in the two runtimes, so every artifact sha256
	// differed, so no Go-exported pack could ever verify in Python. Caught
	// by TestPackWhitespaceMatchesCPython comparing member bytes; nothing
	// in the Go-only round-trip tests could see it, because both ends of
	// those spoke the same wrong dialect.
	//
	// pyval.DumpsCompactPy is that encoder and it already existed — the
	// third time this chunk that the fix was a helper nobody looked for.
	scrubJSONLLine := func(line string) string {
		obj, err := pyval.LoadsOrdered(line)
		if err != nil {
			return scrubText(line) // `except json.JSONDecodeError`
		}
		// NAMED RESIDUAL — a LONE SURROGATE in a row's string value.
		// json.loads keeps it and json.dumps writes `\ud800` back;
		// LoadsOrdered decodes through encoding/json, which rewrites it to
		// U+FFFD, so the port ships a different character in a durable
		// shared store. Not fixed here because the fix is a string decoder
		// inside LoadsOrdered, which every caller in the port shares.
		//
		// Worth naming at THIS consumer rather than only at LoadsOrdered,
		// because the consequence here is specific and worse than elsewhere:
		// different member bytes, a different artifact sha256, a pack the
		// other runtime cannot verify. Note the asymmetry with the importer
		// one file over — import.go's scanRows calls refuseLoneSurrogates
		// and rejects the row LOUDLY; the exporter rewrites it silently.
		// PyNumbers between the load and the dump: CPython has no numeric
		// literal to preserve across a loads/dumps pair, so `1e3` comes back
		// as `1000.0`. Without it the port shipped the source literal, the
		// member bytes differed, and the artifact sha256 in the hashed
		// manifest differed with them.
		walked := scrub.Walk(pyval.PyNumbers(obj), scrubText)
		out, derr := pyval.DumpsCompactPy(walked)
		if derr != nil {
			// A row whose decoded shape the writer cannot render degrades
			// to raw-text scrubbing rather than silently dropping the row.
			return scrubText(line)
		}
		return out
	}

	var artifacts []map[string]any
	var files []tarEntry
	var sections []string

	addText := func(cls, rel, rawText string) {
		scrubbed := scrubText(rawText)
		artifacts = append(artifacts, map[string]any{
			"class": cls, "path": "artifacts/" + rel, "sha256": sha256Text(scrubbed),
		})
		files = append(files, tarEntry{"artifacts/" + rel, []byte(scrubbed)})
		sections = append(sections, reviewSection(cls, rel, scrubbed))
	}

	// `json.loads`, not encoding/json — and this is the one predicate in the
	// exporter where the difference SHIPS SOMETHING.
	//
	// It is the gate that keeps `minted_from="prompt"` rows (the provenance
	// quarantine, the db37d525 contamination answer) out of a curated pack.
	// Two ways the strict decoder let one through, both measured:
	//
	//   - A row carrying a non-finite float. CPython's json.dumps WRITES the
	//     bare tokens NaN / Infinity, and its json.loads reads them back;
	//     encoding/json refuses both. So a lessons row with `"score": NaN` —
	//     a row CPython itself wrote — failed to decode here, returned
	//     false, and shipped WITH its prompt stamp. LoadsOrdered masks the
	//     tokens the way Python accepts them.
	// A DUPLICATE `minted_from` — `{"minted_from":"outcome","minted_from":
	// "prompt"}` — is the other way this predicate could have been defeated,
	// since json.loads keeps the LAST value and Obj.Get returns the FIRST.
	// It is not a bug here, because decodeOrdered already collapses
	// duplicates last-wins the way CPython does, and Get therefore never
	// sees two. Measured, not assumed: an earlier draft of this comment
	// claimed the leak and added a hand-rolled last-wins scan for it, and
	// the mutation that removed the scan was an equivalent mutant. The
	// fixture stays — it is now this consumer's pin on decodeOrdered's
	// collapse, which nothing else here would notice losing.
	//
	// Valid-JSON non-object rows (null, [], "str") are not lessons but must
	// not abort the export — they ship as before, scrubbed. That is the
	// `isinstance(obj, dict)` arm, and falling out of the type switch is it.
	quarantinedRow := func(line string) bool {
		v, err := pyval.LoadsOrdered(line)
		if err != nil {
			return false // `except json.JSONDecodeError`
		}
		obj, ok := v.(pyval.Obj)
		if !ok {
			return false
		}
		minted, _ := obj.Get("minted_from")
		return minted == "prompt"
	}

	addJSONL := func(cls, rel, path string, dropQuarantined bool) error {
		// `_read_jsonl_rows`: `if not path.exists(): return []`, then
		// read_text — so a missing store is empty and a byte-tainted one
		// aborts the export. Universal newlines matter here too even though
		// every row is split immediately: a "\r\n" store row would otherwise
		// keep its "\r" in the SCRUBBED member bytes... which SplitLines
		// already strips. It is the whole-file readers that need it. Using
		// the same helper anyway, because two spellings of read_text is how
		// the halves got separated in the first place.
		text, err := pyval.ReadText(path)
		if os.IsNotExist(err) {
			return nil
		}
		if err != nil {
			return err
		}
		raw := []byte(text)
		var rows []string
		// `[ln for ln in text.splitlines() if ln.strip()]`, and BOTH halves
		// of that are wrong when spelled with the strings package. Python
		// splits on ten separators, not just "\n" — a row carrying \x0b or
		// U+2028 is two rows there and one row here — and `ln.strip()` is
		// falsy for a line of information separators that TrimSpace leaves
		// non-empty. These rows are the pack payload: they are hashed into
		// pack.json, so either divergence ships a pack the other runtime
		// cannot verify.
		for _, ln := range pytext.SplitLines(string(raw)) {
			if pytext.Strip(ln) != "" {
				rows = append(rows, ln)
			}
		}
		quarantinedSkipped := 0
		if dropQuarantined {
			// Provenance-gate rows must not ship to a box whose importer may
			// not honor the stamp. Skipped, not scrubbed — the count rides
			// the manifest.
			kept := rows[:0:0]
			for _, ln := range rows {
				if quarantinedRow(ln) {
					quarantinedSkipped++
				} else {
					kept = append(kept, ln)
				}
			}
			rows = kept
		}
		var scrubbed []string
		for _, ln := range rows {
			scrubbed = append(scrubbed, scrubJSONLLine(ln))
		}
		if len(scrubbed) == 0 && quarantinedSkipped == 0 {
			return nil // a young workspace has no rules yet
		}
		// When filtering emptied the artifact, still emit a zero-row entry:
		// a reviewer must be able to tell "no lessons existed" from "all
		// lessons were withheld".
		entry := map[string]any{
			"class": cls, "path": "artifacts/" + rel, "rows": len(scrubbed),
		}
		if quarantinedSkipped > 0 {
			entry["quarantined_rows_skipped"] = quarantinedSkipped
		}
		content := ""
		for _, ln := range scrubbed {
			content += ln + "\n"
		}
		entry["sha256"] = sha256Text(content)
		artifacts = append(artifacts, entry)
		files = append(files, tarEntry{"artifacts/" + rel, []byte(content)})
		sections = append(sections, reviewSection(cls, rel, content))
		return nil
	}

	memDir := filepath.Join(ws, "memory")
	for _, kind := range []struct{ dir, cls string }{
		{"skills", "skill_md"}, {"personas", "persona_md"},
	} {
		d := filepath.Join(ws, kind.dir)
		names, _ := filepath.Glob(filepath.Join(d, "*.md"))
		// `sorted(skills_dir.glob("*.md"))` orders Path objects. The order
		// reaches the pack's artifact list and therefore its canonical
		// digest, so a divergence here is a pack two engines disagree about.
		sort.Slice(names, func(i, j int) bool { return pypath.FSLess(names[i], names[j]) })
		for _, f := range names {
			// `if f.is_file()`. Python filters the glob; the port never
			// did, and while the read merely `continue`d that was invisible.
			// Turning the read into a propagating error turned a benign
			// divergence into a fatal one: a DIRECTORY named `*.md` now
			// aborts the whole export where CPython exports fine.
			//
			// A fix is evidence about its siblings, and this is the other
			// direction of the same lens — a fix can also make a dormant
			// divergence load-bearing. The guard the fix relied on was
			// never there.
			if st, serr := os.Stat(f); serr != nil || !st.Mode().IsRegular() {
				continue
			}
			// read_text, and the error PROPAGATES. Python's
			// `f.read_text(encoding="utf-8")` here is not inside a try —
			// only the include_runs loop below catches UnicodeDecodeError,
			// deliberately, because run artifacts may be binary. A skill
			// file that is not UTF-8 aborts the whole CPython export; the
			// port used to `continue` and ship a pack SHORT one artifact,
			// silently, which is the worse of the two answers: the operator
			// gets a pack that looks complete.
			text, err := pyval.ReadText(f)
			if err != nil {
				return nil, err
			}
			addText(kind.cls, kind.dir+"/"+filepath.Base(f), text)
		}
	}

	steps := []struct {
		cls, rel, path      string
		dropQuarantined, on bool
	}{
		{"skill_records", "memory/skills.jsonl", filepath.Join(memDir, "skills.jsonl"), false, true},
		{"rules", "memory/standing_rules.jsonl", filepath.Join(memDir, "standing_rules.jsonl"), false, true},
		{"hypotheses", "memory/hypotheses.jsonl", filepath.Join(memDir, "hypotheses.jsonl"), false, true},
		{"lessons", "memory/long/lessons.jsonl", filepath.Join(memDir, "long", "lessons.jsonl"), true, true},
		{"lessons_medium", "memory/medium/lessons.jsonl", filepath.Join(memDir, "medium", "lessons.jsonl"), true, opts.IncludeMedium},
		{"knowledge_nodes", "memory/knowledge_nodes.jsonl", filepath.Join(memDir, "knowledge_nodes.jsonl"), false, opts.IncludeKnowledge},
		{"knowledge_edges", "memory/knowledge_edges.jsonl", filepath.Join(memDir, "knowledge_edges.jsonl"), false, opts.IncludeKnowledge},
	}
	for _, st := range steps {
		if !st.on {
			continue
		}
		if err := addJSONL(st.cls, st.rel, st.path, st.dropQuarantined); err != nil {
			return nil, fmt.Errorf("export %s: %w", st.rel, err)
		}
	}
	if opts.IncludePlaybook {
		pb := filepath.Join(ws, "playbook.md")
		// `if pb.exists(): _add_text_artifact(..., pb.read_text(...))` — a
		// missing playbook is not an error, anything else about it is.
		if _, serr := os.Stat(pb); serr == nil {
			text, err := pyval.ReadText(pb)
			if err != nil {
				return nil, err
			}
			addText("playbook", "playbook.md", text)
		}
	}

	manifest := map[string]any{
		"pack_format": PackFormat,
		"name":        opts.Name,
		"created_at":  nowISO(),
		"origin": map[string]any{
			"label":            opts.Label,
			"maro_version":     "go-port",
			"scrubber_version": ScrubberVersion,
		},
		"artifacts": anySlice(artifacts),
		"review": map[string]any{
			"human_reviewed": false, "reviewed_at": nil,
			"review_manifest_sha256": nil, "review_payload_sha256": nil,
		},
		"trust_policy": "demote-to-hypothesis",
	}
	reviewMD := buildReviewMD(manifest, sections)

	packPath := filepath.Join(out, opts.Name+ArchiveSuffix)
	manifestJSON, err := manifestBytes(manifest)
	if err != nil {
		return nil, err
	}
	entries := append([]tarEntry{
		{"pack.json", append(manifestJSON, '\n')},
		{"REVIEW.md", []byte(reviewMD)},
	}, files...)
	if err := writeArchive(packPath, entries); err != nil {
		return nil, err
	}
	companion := reviewCompanionPath(packPath)
	if err := os.WriteFile(companion, []byte(reviewMD), 0o644); err != nil {
		return nil, err
	}
	return &ExportResult{PackPath: packPath, ReviewPath: companion, Manifest: manifest}, nil
}

func anySlice(in []map[string]any) []any {
	out := make([]any, len(in))
	for i, m := range in {
		out[i] = m
	}
	return out
}
