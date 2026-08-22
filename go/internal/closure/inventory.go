package closure

import (
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// projectFileInventory is a bounded relative-path listing of the
// verification cwd — ground truth for the closure plan so checks probe
// files that actually EXIST instead of filenames the LLM guesses from
// the work summary (Python 2026-07-09 dogfood batch: two known-good
// runs false-negatived by checks against invented names while the real
// deliverables sat next to them). Returns "" when root is missing or
// not a dir; skips VCS/cache dirs and .lock files; caps at `cap`
// entries with a truncation marker so a big tree can't blow up the
// prompt.
func projectFileInventory(root string, cap int) string {
	fi, err := os.Stat(root)
	if err != nil || !fi.IsDir() {
		return ""
	}
	skipDirs := map[string]bool{".git": true, "__pycache__": true, "node_modules": true, ".venv": true}
	var entries []string
	var walk func(dir, rel string) bool
	walk = func(dir, rel string) bool {
		items, err := os.ReadDir(dir)
		if err != nil {
			return true
		}
		sort.Slice(items, func(i, j int) bool { return items[i].Name() < items[j].Name() })
		// Files in this dir first, then subdirs — matching os.walk's
		// per-directory visit order closely enough for a prompt listing.
		for _, it := range items {
			if it.IsDir() {
				continue
			}
			if strings.HasSuffix(it.Name(), ".lock") {
				continue
			}
			p := it.Name()
			if rel != "" {
				p = filepath.Join(rel, it.Name())
			}
			entries = append(entries, p)
			if len(entries) >= cap {
				entries = append(entries, "... (truncated at "+strconv.Itoa(cap)+" files)")
				return false
			}
		}
		for _, it := range items {
			if !it.IsDir() || skipDirs[it.Name()] {
				continue
			}
			sub := it.Name()
			if rel != "" {
				sub = filepath.Join(rel, it.Name())
			}
			if !walk(filepath.Join(dir, it.Name()), sub) {
				return false
			}
		}
		return true
	}
	walk(root, "")
	return strings.Join(entries, "\n")
}
