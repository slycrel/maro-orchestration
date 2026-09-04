// Package workspace resolves the successor's own root (design note §13 —
// never the Python engine's), announces it before any write (the 2026-08-16
// live-ledger scar, made structural), and holds the single-process lease
// (§2, D12).
package workspace

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

// EnvOverride is the one environment variable that moves the root.
const EnvOverride = "MARO_GO_WORKSPACE"

// DefaultRel is the default root, relative to $HOME.
const DefaultRel = ".maro-go/workspace"

// Root is a resolved workspace root. Writers require an announced Root; an
// unannounced one refuses to hand out paths, so the path is printed before
// the first write by construction rather than by discipline.
type Root struct {
	path      string
	source    string // "env" | "default"
	announced bool
}

// Resolve reads the override or the default. It never creates anything.
func Resolve() (*Root, error) {
	if p := os.Getenv(EnvOverride); p != "" {
		abs, err := filepath.Abs(p)
		if err != nil {
			return nil, err
		}
		return &Root{path: abs, source: "env"}, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, fmt.Errorf("workspace: no home directory and %s unset: %w", EnvOverride, err)
	}
	return &Root{path: filepath.Join(home, DefaultRel), source: "default"}, nil
}

// Announce prints the resolved root and its source to w and marks the Root
// usable. Every process prints this line once, before any write.
func (r *Root) Announce(w io.Writer) *Root {
	fmt.Fprintf(w, "workspace: %s (%s)\n", r.path, r.source)
	r.announced = true
	return r
}

// ErrUnannounced is returned by Path for a Root nobody announced.
var ErrUnannounced = errors.New("workspace: root not announced — call Announce before any write")

// Path joins under the root. It refuses until Announce has run.
func (r *Root) Path(parts ...string) (string, error) {
	if !r.announced {
		return "", ErrUnannounced
	}
	return filepath.Join(append([]string{r.path}, parts...)...), nil
}

// String is the path; safe to log.
func (r *Root) String() string { return r.path }

// Ensure creates the root directory tree used by step 1: thoughts/, and the
// lease location. Later steps add their own subdirectories.
func (r *Root) Ensure() error {
	for _, d := range []string{"", "thoughts"} {
		p, err := r.Path(d)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(p, 0o755); err != nil {
			return err
		}
	}
	return nil
}
