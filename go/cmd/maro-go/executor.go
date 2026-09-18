package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/secrets"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// The operator's isolation setting, wired the same way on every command
// that runs work: `run`, `runs resume` and `serve` (a resume that wired the
// lane differently from the run would execute the rest of a required-
// container run on the host).
//
// Nothing here creates a container or an image: the operator builds the
// image and seeds the auth volume once, and the preflight says what is
// missing. The defaults name Python main's executor image and auth volume
// on purpose — it is the same pinned CLI with the same login inside, and
// the two engines sharing one built artifact is cheaper and more honest
// than a second image that drifts (the secrets store is shared the same
// way).
const (
	envExecutor    = "MARO_GO_EXECUTOR"
	envExecImage   = "MARO_GO_EXECUTOR_IMAGE"
	envExecVolume  = "MARO_GO_EXECUTOR_AUTH_VOLUME"
	envExecMount   = "MARO_GO_EXECUTOR_AUTH_MOUNT"
	envExecHome    = "MARO_GO_EXECUTOR_HOME"
	envExecNetwork = "MARO_GO_EXECUTOR_NETWORK"
)

// executorFlags is what the commands parse: the policy and the image, each
// falling back to its environment variable.
type executorFlags struct {
	policy string
	image  string
}

// parse handles the two flags; it returns true when it consumed args[i]
// (and advances i past a value). A flag with no value is an ERROR, not a
// default: `--executor` with nothing after it used to resolve to the empty
// policy, which is `off` — an operator typo at the isolation boundary
// silently choosing the least safe lane (review r1).
func (e *executorFlags) parse(args []string, i *int) (bool, error) {
	switch args[*i] {
	case "--executor":
		v, err := flagValue(args, i, "--executor")
		e.policy = v
		return true, err
	case "--executor-image":
		v, err := flagValue(args, i, "--executor-image")
		e.image = v
		return true, err
	}
	return false, nil
}

// flagValue takes the next argument as name's value, refusing an absent one
// and another flag standing in for one.
func flagValue(args []string, i *int, name string) (string, error) {
	*i++
	if *i >= len(args) || strings.HasPrefix(args[*i], "--") {
		return "", fmt.Errorf("%s needs a value", name)
	}
	return args[*i], nil
}

// wire puts the backend on the container lane when the operator asked for
// one. The BACKEND is then the policy's one owner: every driver that runs
// it records the policy by asking it (invoke.Isolated), so nothing has to
// pass the same setting twice. A degrade under `on`, and a container the
// engine could not end, are announced to the operator's own stream once
// each; the record carries them too (run.ExecutorViewOf), so the notice is
// a courtesy, not the evidence.
//
// It MUST be called on every command that can run work, including when
// that command has no subprocess backend: a policy the operator asked for
// and nothing can keep has to fail here, before a journal is touched.
func (e *executorFlags) wire(s *invoke.Subprocess, a *workspace.Announced, errw io.Writer) error {
	policy, image := e.policy, e.image
	if policy == "" {
		policy = os.Getenv(envExecutor)
	}
	if image == "" {
		image = os.Getenv(envExecImage)
	}
	pol, err := invoke.ExecutorPolicyOf(policy)
	if err != nil {
		return fmt.Errorf("--executor: %w", err)
	}
	if pol == invoke.ExecutorOff {
		if image != "" {
			return fmt.Errorf("--executor-image %s names an image but --executor is off", image)
		}
		return nil
	}
	if s == nil {
		return fmt.Errorf("--executor %s: this command has no subprocess backend to isolate", pol)
	}
	forbidden, sealed, err := hostRoots(a)
	if err != nil {
		return fmt.Errorf("--executor %s: %w", pol, err)
	}
	s.Container = invoke.NewContainer(image, os.Getenv(envExecVolume), os.Getenv(envExecMount), os.Getenv(envExecHome), os.Getenv(envExecNetwork), forbidden...)
	s.Container.Sealed = sealed
	s.Isolation = pol
	s.Notify = func(note string) { fmt.Fprintln(errw, "executor:", note) }
	fmt.Fprintf(errw, "executor: %s, image %s\n", pol, s.Container.Image)
	return nil
}

// hostRoots are the two lists the launcher refuses binds against, and it is
// the CLI that knows where they are.
//
// FORBIDDEN is a contains-rule: a bind that IS one of these, or CONTAINS
// one, is refused — this engine's workspace (the journal, the thought
// store, the drop directory's parent), the user's home, the filesystem
// root. A DESCENDANT is fine and is the normal case: the drop directory is
// inside the workspace and binding it is the point. Without this,
// `--work <workspace>` would hand the orchestration to the worker
// read-write and still record "container" (review r1; main's
// `_forbidden_mount_roots`).
//
// SEALED is a tree: nothing at or inside it is ever bound. The secrets
// store is the case the contains-rule could not express — `--work
// ~/.maro/secrets` is a DESCENDANT of the forbidden `~/.maro` and walked
// straight past it, handing the sops file and the age identity to a worker
// read-write (review r2).
//
// It fails CLOSED. A box whose home directory or secrets store cannot be
// resolved is a box where these protections would silently shorten to
// nothing, and an isolation that protects less than it says is worse than
// no isolation at all.
func hostRoots(a *workspace.Announced) (forbidden, sealed []string, err error) {
	forbidden = []string{string(filepath.Separator)}
	if a != nil {
		forbidden = append(forbidden, a.String())
	}
	home, herr := os.UserHomeDir()
	if herr != nil {
		return nil, nil, fmt.Errorf("the host roots a container must never be given cannot be resolved: %v", herr)
	}
	forbidden = append(forbidden, home, filepath.Join(home, ".maro"), filepath.Join(home, ".maro-go"))
	store := strings.TrimSpace(secrets.Open().Dir)
	if store == "" {
		return nil, nil, fmt.Errorf("the secrets store directory cannot be resolved, so nothing can be kept out of the container: set %s and resume", secrets.EnvDir)
	}
	// ABSOLUTE, every one of them. A relative root cannot be compared with a
	// resolved bind source (`filepath.Rel` refuses the mixed pair), so a
	// relative `MARO_SECRETS_DIR` made the sealed check read "not
	// contained" for its own store (review r3). Resolving here, once, where
	// the process's own cwd is still meaningful, is the only place it can be
	// done honestly.
	abs := func(r string) (string, error) {
		a, aerr := filepath.Abs(r)
		if aerr != nil {
			return "", fmt.Errorf("the host root %s cannot be resolved to an absolute path: %v", r, aerr)
		}
		return a, nil
	}
	for i, r := range forbidden {
		if forbidden[i], err = abs(r); err != nil {
			return nil, nil, err
		}
	}
	if store, err = abs(store); err != nil {
		return nil, nil, err
	}
	return forbidden, []string{store}, nil
}
