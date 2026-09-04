// Package workspace resolves the successor's own root (design note §13 —
// never the Python engine's), announces it before any write (the 2026-08-16
// live-ledger scar, made structural), and holds the single-process lease
// (§2, D12) as an OS advisory lock.
package workspace

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

// EnvOverride is the one environment variable that moves the root. Set but
// empty counts as unset.
const EnvOverride = "MARO_GO_WORKSPACE"

// DefaultRel is the default root, relative to $HOME.
const DefaultRel = ".maro-go/workspace"

// Root is a resolved workspace root. Writers require an Announced root; an
// unannounced one refuses to hand out paths, so the path is printed before
// the first write by construction rather than by discipline.
type Root struct {
	path   string
	source string // "env" | "default"
}

// Announced is the capability a writer needs. It is only ever produced by a
// successful Announce, so holding one proves the operator saw the path.
type Announced struct{ r *Root }

// Resolve reads the override or the default. It never creates anything and
// never follows symlinks; the path is cleaned so a trailing slash cannot
// produce two spellings of one root.
func Resolve() (*Root, error) {
	if p := os.Getenv(EnvOverride); strings.TrimSpace(p) != "" {
		abs, err := filepath.Abs(p)
		if err != nil {
			return nil, err
		}
		return &Root{path: filepath.Clean(abs), source: "env"}, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, fmt.Errorf("workspace: no home directory and %s unset: %w", EnvOverride, err)
	}
	return &Root{path: filepath.Join(home, DefaultRel), source: "default"}, nil
}

// Announce prints the resolved root and its source to w. Only a successful,
// complete write yields the Announced capability; a failed write yields
// nothing, so no writer can proceed without the operator having seen the
// line.
func (r *Root) Announce(w io.Writer) (*Announced, error) {
	line := fmt.Sprintf("workspace: %s (%s)\n", r.path, r.source)
	n, err := io.WriteString(w, line)
	if err != nil {
		return nil, fmt.Errorf("workspace: announce failed: %w", err)
	}
	if n != len(line) {
		return nil, errors.New("workspace: announce short write")
	}
	return &Announced{r: r}, nil
}

// String is the path; safe to log.
func (r *Root) String() string { return r.path }

// Path joins under the announced root.
func (a *Announced) Path(parts ...string) string {
	return filepath.Join(append([]string{a.r.path}, parts...)...)
}

// String is the path.
func (a *Announced) String() string { return a.r.path }

// Ensure creates the root directory tree used by step 1: thoughts/. Later
// steps add their own subdirectories.
func (a *Announced) Ensure() error {
	for _, d := range []string{"", "thoughts"} {
		if err := os.MkdirAll(a.Path(d), 0o755); err != nil {
			return err
		}
	}
	return nil
}
