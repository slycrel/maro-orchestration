package closure

import (
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
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
		// pypath.FSLess, not a bare `<`. closure_verify.py:697 and :700 are
		// TWO real sorted() calls -- `dirnames[:] = sorted(...)` and
		// `for fn in sorted(filenames)` -- and CPython compares the
		// surrogateescape-DECODED name while `<` on a Go string compares
		// raw bytes. The two part on any entry that is not valid UTF-8.
		//
		// This shipped as the byte spelling for months under a
		// dirSortAllowlist row asserting "os.walk does not sort", which is
		// true of artifact_check's walk and false of this one. Measured
		// over a tree holding a\x80z.txt, a-e-acute-z.txt, d\x80/k.txt and
		// d-e-acute/k.txt: CPython lists e-acute before \x80, Go the
		// reverse -- and AT THE CAP the two engines name DIFFERENT FILES,
		// which is the W24 shape this whole class exists to prevent. The
		// listing is ground truth for the closure plan, so a truncated
		// inventory naming a different file is a false-negative closure
		// verdict that appears on one runtime only (adversarial r8,
		// MEDIUM, found independently by both reviewer seats).
		sort.SliceStable(items, func(i, j int) bool {
			return pypath.FSLess(items[i].Name(), items[j].Name())
		})
		// Files in this dir first, then subdirs — matching os.walk's
		// per-directory visit order closely enough for a prompt listing.
		//
		// isDir, not it.IsDir(). os.ReadDir reports the entry's OWN type
		// bits, so a symlink pointing at a directory is not a directory to
		// it — and the entry then falls into the file loop below and is
		// emitted as a file. os.walk splits on `entry.is_dir()`, which
		// FOLLOWS the link, so CPython puts that name in `dirnames`, never
		// emits it, and (followlinks defaults to False) never descends into
		// it either. Measured over a root holding `linkdir -> real/`,
		// `real/inner.txt` and `z.txt`: CPython returns
		// "z.txt\nreal/inner.txt", and the byte-typed version returned
		// linkdir as a file — at cap=1 naming a directory link where
		// CPython names z.txt (adversarial r9, MEDIUM, codex seat).
		//
		// A DANGLING symlink stays a file in both: scandir's is_dir()
		// returns False when the stat fails, and so does isDir here.
		for _, it := range items {
			if isDir(dir, it) {
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
		// it.IsDir() here, deliberately, and NOT isDir: the two loops split
		// on different questions and os.walk splits on them the same way.
		// Whether a name is EMITTED follows the link (scandir is_dir), and
		// whether it is DESCENDED INTO does not (os.walk's followlinks
		// defaults to False). A symlinked directory is therefore in neither
		// list — not a file, and not a subtree.
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

// isDir answers os.scandir's `entry.is_dir()`: it FOLLOWS a symlink, and
// it is False — not an error — when the stat fails. That second half is
// load-bearing: a dangling symlink is a file to CPython's walk, and a
// helper that propagated the error would have to invent a third answer
// where the Python has two.
func isDir(dir string, e os.DirEntry) bool {
	if e.IsDir() {
		return true
	}
	if e.Type()&os.ModeSymlink == 0 {
		return false
	}
	st, err := os.Stat(filepath.Join(dir, e.Name()))
	return err == nil && st.IsDir()
}
