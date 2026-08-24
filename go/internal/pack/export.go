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
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/config"
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
		if v := strings.TrimSpace(string(out)); v != "" {
			items[v] = true
		}
	}
	cfg, _ := config.Load()
	if extra, ok := cfg["pack"].(map[string]any); ok {
		if lst, ok := extra["export_denylist"].([]any); ok {
			for _, x := range lst {
				if s, ok := x.(string); ok && s != "" {
					items[s] = true
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
	for _, ln := range strings.Split(content, "\n") {
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
			section += fmt.Sprintf("- `%s`\n", strings.TrimSpace(ln))
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

func nowISO() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05.000000-07:00")
}

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

	scrubJSONLLine := func(line string) string {
		dec := json.NewDecoder(strings.NewReader(line))
		dec.UseNumber()
		var obj any
		if err := dec.Decode(&obj); err != nil {
			return scrubText(line)
		}
		walked := scrub.Walk(obj, scrubText)
		canon, err := CanonicalJSON(walked)
		if err != nil {
			// A row whose decoded shape the canonical writer cannot render
			// (should not happen — UseNumber covers numerics) degrades to
			// raw-text scrubbing rather than silently dropping the row.
			return scrubText(line)
		}
		return string(canon)
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

	quarantinedRow := func(line string) bool {
		var obj map[string]any
		if json.Unmarshal([]byte(line), &obj) != nil {
			return false
		}
		return obj["minted_from"] == "prompt"
	}

	addJSONL := func(cls, rel, path string, dropQuarantined bool) error {
		raw, err := os.ReadFile(path)
		if os.IsNotExist(err) {
			return nil
		}
		if err != nil {
			return err
		}
		var rows []string
		for _, ln := range strings.Split(string(raw), "\n") {
			if strings.TrimSpace(ln) != "" {
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
		sort.Strings(names)
		for _, f := range names {
			raw, err := os.ReadFile(f)
			if err != nil {
				continue
			}
			addText(kind.cls, kind.dir+"/"+filepath.Base(f), string(raw))
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
		if raw, err := os.ReadFile(pb); err == nil {
			addText("playbook", "playbook.md", string(raw))
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
