// Adopt — the explicit gate from quarantine to live (§3 "Adoption").
// Ports pack.adopt: promote quarantined skills/personas into the live
// workspace, stamping provenance frontmatter. Never overwrites a live
// file of the same name — that case was already flagged as a conflict at
// import time (local wins, stays in quarantine).
package pack

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// AdoptOpts mirrors adopt()'s surface.
type AdoptOpts struct {
	Label  string
	Target string // resolved workspace
	Items  []string
	All    bool
	DryRun bool
}

// AdoptReport lists what moved and what was refused.
type AdoptReport struct {
	Adopted []map[string]string `json:"adopted"`
	Skipped []map[string]string `json:"skipped"`
}

func stampProvenanceFrontmatter(content, label, now string) string {
	provenance := fmt.Sprintf("imported_from: %s\nadopted_at: %s", label, now)
	if strings.HasPrefix(content, "---\n") {
		if end := strings.Index(content[4:], "\n---"); end != -1 {
			at := 4 + end
			return content[:at] + "\n" + provenance + content[at:]
		}
	}
	return fmt.Sprintf("---\n%s\n---\n%s", provenance, content)
}

// Adopt promotes selected quarantined .md files.
func Adopt(opts AdoptOpts) (*AdoptReport, error) {
	if err := safeLabel(opts.Label); err != nil {
		return nil, err
	}
	ws := opts.Target
	if ws == "" {
		return nil, fmt.Errorf("adopt: workspace not resolved by caller")
	}
	quarantine := filepath.Join(ws, "imports", opts.Label)
	if st, err := os.Stat(quarantine); err != nil || !st.IsDir() {
		return nil, fmt.Errorf("no quarantined imports for label %q under %s",
			opts.Label, quarantine)
	}

	type candidate struct{ kind, name, path string }
	var candidates []candidate
	for _, kind := range []string{"skills", "personas"} {
		matches, _ := filepath.Glob(filepath.Join(quarantine, kind, "*.md"))
		sort.Strings(matches)
		for _, f := range matches {
			candidates = append(candidates, candidate{kind, filepath.Base(f), f})
		}
	}

	var selected []candidate
	if opts.All {
		selected = candidates
	} else {
		if len(opts.Items) == 0 {
			return nil, fmt.Errorf("adopt: specify skill/persona names, or pass --all")
		}
		names := map[string]bool{}
		for _, n := range opts.Items {
			names[n] = true
		}
		found := map[string]bool{}
		for _, c := range candidates {
			stem := strings.TrimSuffix(c.name, ".md")
			if names[c.name] {
				selected = append(selected, c)
				found[c.name] = true
			} else if names[stem] {
				selected = append(selected, c)
				found[stem] = true
			}
		}
		var missing []string
		for n := range names {
			if !found[n] {
				missing = append(missing, n)
			}
		}
		if len(missing) > 0 {
			sort.Strings(missing)
			return nil, fmt.Errorf("adopt: not found in %s: %v", quarantine, missing)
		}
	}

	now := nowISO()
	report := &AdoptReport{Adopted: []map[string]string{}, Skipped: []map[string]string{}}
	for _, c := range selected {
		dest := filepath.Join(ws, c.kind, c.name)
		raw, err := os.ReadFile(c.path)
		if err != nil {
			return nil, err
		}
		stamped := stampProvenanceFrontmatter(string(raw), opts.Label, now)
		if _, err := os.Stat(dest); err == nil {
			report.Skipped = append(report.Skipped, map[string]string{
				"kind": c.kind, "name": c.name, "reason": "already exists locally"})
			continue
		}
		if opts.DryRun {
			report.Adopted = append(report.Adopted, map[string]string{
				"kind": c.kind, "name": c.name})
			continue
		}
		if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
			return nil, err
		}
		// O_EXCL: the exists() check above is advisory; the create is the
		// real never-overwrite guarantee.
		f, err := os.OpenFile(dest, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
		if err != nil {
			if os.IsExist(err) {
				report.Skipped = append(report.Skipped, map[string]string{
					"kind": c.kind, "name": c.name, "reason": "already exists locally"})
				continue
			}
			return nil, err
		}
		if _, err := f.WriteString(stamped); err != nil {
			f.Close()
			return nil, err
		}
		if err := f.Close(); err != nil {
			return nil, err
		}
		report.Adopted = append(report.Adopted, map[string]string{
			"kind": c.kind, "name": c.name})
	}
	return report, nil
}
