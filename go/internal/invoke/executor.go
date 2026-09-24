package invoke

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// The executor: WHERE a call runs.
//
// A tool-bearing execute is the one call that touches the machine — it
// writes files, runs commands, reaches the network. The engine can hand
// that call to the CLI on the host, or to the same CLI inside a container
// that sees only the run's work dir. Which one it was is not an
// implementation detail the journal may leave out: it is part of what the
// backend saw, exactly like the working directory and the tool policy, and
// a run whose executes were supposed to be isolated and silently were not
// is a run whose record lied (Python main, 2026-09-12: a host-lane degrade
// under `on` decrypted the secrets store on the host, and nothing in the
// record said so).
//
// So every call a container-capable backend makes names its executor
// BEFORE dispatch (`Executored`), the attempt records the policy it ran
// under, and the fold holds every call of the attempt to it: under
// `require` a tool-bearing call that ran on the host is refused as a lie,
// whatever else the record says.
//
// One framer, one classifier. The container is a LAUNCHER, not a second
// backend: the stream parser, the redaction, the secrets hand-off, the
// transcript capture and the terminal classification are the same code on
// both lanes, and the only difference is the argv the child process is
// started with. Python main paid for the other arrangement over twenty-two
// review rounds on a forked capture reader; the Go engine keeps the seam
// under the argv.
type ExecutorKind string

const (
	// ExecutorHost: the CLI as a child of this process, on this machine.
	ExecutorHost ExecutorKind = "host"
	// ExecutorContainer: the CLI inside a container, from an image the
	// record names.
	ExecutorContainer ExecutorKind = "container"
)

var executorKinds = map[ExecutorKind]bool{ExecutorHost: true, ExecutorContainer: true}

// notInARef: characters an image reference or an image id never contains.
const notInARef = " \t\n\r\"'\\"

// Executor is where a call ran: committed with the invocation, before
// dispatch, so the decision and the receipt agree. A container call names
// the exact image tag — the tag is the audit (a rebuilt image under the
// same tag names two different worlds, so the tag carries the CLI pin and
// a revision).
type Executor struct {
	Kind  ExecutorKind `json:"kind"`
	Image string       `json:"image,omitempty"`
	// Digest is the image id the daemon resolved the tag to. The TAG is
	// what the operator named and can be rebuilt under; the digest is the
	// world the call actually ran in, so the record carries both (review
	// r1: "the image field is a mutable tag, not the inspected digest").
	Digest string `json:"digest,omitempty"`
	// Network is the docker network mode the call ran under: it changes
	// what the isolated call could reach, so it belongs to the exposure
	// and not to the engine's configuration alone.
	Network string `json:"network,omitempty"`
}

func (e *Executor) validate() error {
	if e == nil {
		return nil
	}
	if !executorKinds[e.Kind] {
		return fmt.Errorf("invocation: executor kind %q out of vocabulary", e.Kind)
	}
	if e.Kind == ExecutorContainer && e.Image == "" {
		return errors.New("invocation: a container executor with no image")
	}
	// The image and the digest are REFERENCES, not prose: a value carrying
	// whitespace or a quote is a shell accident or a forged record dressed
	// as one, and the declared contract's pattern already says so. The
	// validator has to say it too, or the pattern is a claim nothing
	// enforces (review r2).
	if strings.ContainsAny(e.Image, notInARef) {
		return fmt.Errorf("invocation: executor image %q is not an image reference", e.Image)
	}
	if strings.ContainsAny(e.Digest, notInARef) {
		return fmt.Errorf("invocation: executor digest %q is not an image id", e.Digest)
	}
	if e.Kind != ExecutorContainer {
		switch {
		case e.Image != "":
			return fmt.Errorf("invocation: a %s executor naming image %q", e.Kind, e.Image)
		case e.Digest != "":
			return fmt.Errorf("invocation: a %s executor naming digest %q", e.Kind, e.Digest)
		case e.Network != "":
			return fmt.Errorf("invocation: a %s executor naming network %q", e.Kind, e.Network)
		}
	}
	return nil
}

// ExecutorPolicy is the operator's setting for the tool-bearing lane.
type ExecutorPolicy string

const (
	// ExecutorOff: the host lane, always (the default, and what every
	// journal before this field recorded).
	ExecutorOff ExecutorPolicy = "off"
	// ExecutorOn: the container when it can run, the host when it cannot —
	// and the degrade is RECORDED (the invocation says host) and announced,
	// never silent.
	ExecutorOn ExecutorPolicy = "on"
	// ExecutorRequire: the container or nothing. A tool-bearing call that
	// cannot be containerized is refused before dispatch; the attempt ends
	// and says what to fix. Never a host degrade — that is the whole point
	// of asking for it.
	ExecutorRequire ExecutorPolicy = "require"
)

var executorPolicies = map[ExecutorPolicy]bool{ExecutorOff: true, ExecutorOn: true, ExecutorRequire: true}

// ExecutorPolicyOf reads a policy from an operator string ("" ⇒ off).
func ExecutorPolicyOf(s string) (ExecutorPolicy, error) {
	if s == "" {
		return ExecutorOff, nil
	}
	p := ExecutorPolicy(s)
	if !executorPolicies[p] {
		return "", fmt.Errorf("executor policy %q out of vocabulary (off|on|require)", s)
	}
	return p, nil
}

// ValidExecutorPolicy says whether a recorded policy is in vocabulary.
func ValidExecutorPolicy(p ExecutorPolicy) bool { return executorPolicies[p] }

// Executored is a backend whose calls run somewhere the journal must name.
// ExecutorFor is asked BEFORE the invocation is committed and must answer
// what a call of this request will actually run in — so it is where the
// preflight happens and where a refusal belongs. An error means nothing
// happened: no invocation, no call. An EMPTY kind means this backend does
// not answer for an executor and the record says nothing (a scripted
// backend); it is not a way to say "the host", which a host call states.
type Executored interface {
	ExecutorFor(ctx context.Context, req Request) (Executor, error)
}

// Isolated is a backend that runs its tool-bearing calls under an executor
// policy. The ATTEMPT records that policy (run.ConfigSnapshot.Executor) and
// the fold holds every call to it, so the policy has exactly ONE owner —
// the backend that enforces it. A driver that was TOLD a policy separately
// could be told a different one than the backend enforces, and the first
// thing that would notice is the fold refusing the run's own records
// (review r1: three CLI call sites kept two fields equal by hand).
type Isolated interface {
	ExecutorPolicy() ExecutorPolicy
}

// ErrExecutorUnavailable: the executor the operator required cannot run
// right now (no docker, no image, no auth volume). The reason says what to
// fix; the run stops before the call and resumes when it is fixed.
var ErrExecutorUnavailable = errors.New("invoke: the required executor cannot run")

// Launch is one child process the backend wants started.
type Launch struct {
	Bin   string   // the CLI as this process resolved it (host path)
	Args  []string // its arguments
	Cwd   string   // the working directory the call runs in ("" = this process's)
	Env   []string // extra "NAME=value" for a tool-bearing call (the secrets injection)
	Files []string // absolute paths the child must be able to READ (the secrets hand-off file)
	// Writable are absolute paths the child must be able to WRITE: the
	// operator-question file ($MARO_ASK) and the derived-secrets drop. A
	// lane that carried the READ paths and not these would take the ask
	// lane away from a containerized worker without saying so — the
	// silent capability loss this whole chunk exists to refuse.
	Writable []string
}

// Launched is one prepared call: the argv to run, the directory to run it
// in, the environment the CHILD PROCESS OF THIS ENGINE gets ("" dir and nil
// env mean "inherit"), and the way to END the work when killing the child
// does not end it.
type Launched struct {
	Argv []string
	Dir  string
	Env  []string
	// Stop ends the work this call started. On the host lane the child IS
	// the work, so Stop is nil and the engine's own kill is the whole
	// story. On the container lane it is NOT: killing the `docker run`
	// client leaves the container running (measured on this box,
	// 2026-09-18 — the client was SIGKILLed and `docker ps` still showed
	// the container up), so a timed-out worker would go on writing the
	// bound directories and calling tools after the engine had recorded
	// the attempt failed. Best-effort and idempotent: the container may be
	// gone already.
	Stop func(ctx context.Context) error
}

// Launcher turns a Launch into the call that is actually executed. It is
// the only difference between the host and container lanes.
type Launcher interface {
	// Executor is what the journal records for a call this launcher makes.
	Executor() Executor
	// Wrap prepares one call. Secret VALUES never reach argv.
	Wrap(l Launch) (Launched, error)
	// Preflight answers whether this launcher can run a call at all right
	// now. A nil error means it can.
	Preflight(ctx context.Context) error
}

// ---- the host lane --------------------------------------------------------

// HostLauncher runs the CLI as a child of this process.
type HostLauncher struct{}

func (HostLauncher) Executor() Executor { return Executor{Kind: ExecutorHost} }

func (HostLauncher) Wrap(l Launch) (Launched, error) {
	argv := append([]string{l.Bin}, l.Args...)
	var env []string
	if len(l.Env) > 0 {
		env = append(os.Environ(), l.Env...)
	}
	// no Stop: the child process IS the work, and the engine kills it
	return Launched{Argv: argv, Dir: l.Cwd, Env: env}, nil
}

func (HostLauncher) Preflight(context.Context) error { return nil }

// ---- the container lane ---------------------------------------------------

// Container runs the CLI inside a container from Image, through the docker
// CLI. The image carries the pinned claude binary; the auth volume carries
// its login. Nothing about the container is created here: the operator
// builds the image and seeds the volume (the engine never runs `docker
// build` for the executor image itself — an env-request layer is its own
// lane), and Preflight reports what is missing.
//
// What the container sees, and nothing else: the run's work dir, bound at
// the SAME absolute path (so a path the worker reads out of its own record
// means the same thing in both worlds), the per-call secrets hand-off file
// read-only, and the auth volume. Secret VALUES ride the docker client's
// own environment as bare `-e NAME` flags, so they never appear in an argv
// a host process listing can read.
type Container struct {
	Image      string
	AuthVolume string
	// AuthMount is where the auth volume is mounted: the CLI's own state
	// directory INSIDE Home, not Home itself — a volume over the whole home
	// would shadow everything else the image put there.
	AuthMount string
	// AuthEnv names the variable carrying a long-lived CLI token
	// (`claude setup-token`). When THIS process's environment holds it, the
	// call gets it the way it gets a secret — the name on the command line,
	// the value in the docker client's environment — and the volume's
	// login stops being the gate: a token outlives the refresh-token
	// session the volume holds, which is what expired under every
	// container run on 2026-08-12, 09-12 and 09-18. The worker can read
	// the token either way (the volume's credentials file is mounted into
	// it too), so the exposure is the same; the value is redacted from what
	// comes back like any injected secret.
	AuthEnv string
	// Home is HOME inside the container. Fixed, not the invoking user's
	// home, so the volume mounts at a known path whatever uid the call
	// runs as.
	Home    string
	Network string
	// Docker is the docker binary ("docker" by default).
	Docker string
	// Forbidden are host roots that must NEVER be bound into a container,
	// nor contain a bind source: the engine's own workspace (the journal,
	// the thought store, the secrets drop), the secrets store, the user's
	// home, the filesystem root. The CLI names them, because it is what
	// knows where they are; the launcher refuses before dispatch. Without
	// it `--executor require --work <workspace>` would mount the
	// orchestration read-write into the worker and still record
	// "container" (main's `_forbidden_mount_roots`, review r1).
	Forbidden []string
	// Sealed are host trees nothing inside may be bound, at any depth: the
	// secrets store, whose age identity and sops file are the one thing a
	// worker must never hold. Forbidden is a CONTAINS rule, so it allows
	// descendants on purpose (the drop directory lives inside the
	// workspace) — which is exactly why `--work ~/.maro/secrets` walked
	// past it (review r2).
	Sealed []string
	// Owner is the pid the container is labelled with, so an operator (or
	// a later sweep) can tell a container whose engine is alive from one
	// whose engine died. 0 ⇒ this process.
	Owner int
	// Exec runs one short docker command and returns its combined output.
	// Nil ⇒ the real one. The seam the tests use: nothing here needs a
	// daemon.
	Exec  func(ctx context.Context, argv []string) ([]byte, error)
	nameN atomic.Uint64

	// mu guards the preflight's memory. `ready` is not a hint: a second
	// goroutine reading it unsynchronized is a data race, and two calls
	// could run the probe sequence over each other (review r1).
	mu     sync.Mutex
	ready  bool
	digest string
}

// ContainerDefaults are the identities the engine uses when the operator
// names none. The image tag is Python main's executor image: the two
// engines share the built artifact on purpose (like the secrets store),
// because it is the same CLI, pinned, with the same login inside.
const (
	DefaultExecutorImage = "maro-executor:2.1.210-r3"
	DefaultAuthVolume    = "maro-claude-auth"
	DefaultAuthEnv       = "CLAUDE_CODE_OAUTH_TOKEN"
	DefaultContainerHome = "/home/maro"
	DefaultAuthMount     = DefaultContainerHome + "/.claude"
	DefaultExecutorNet   = "bridge"
	probeTimeout         = 8 * time.Second
	// the login check starts a container, so it gets its own budget
	loginTimeout = 30 * time.Second
)

// NewContainer is the launcher with the defaults filled in.
func NewContainer(image, volume, mount, home, network string, forbidden ...string) *Container {
	c := &Container{Image: image, AuthVolume: volume, AuthMount: mount, Home: home, Network: network, AuthEnv: DefaultAuthEnv, Docker: "docker", Forbidden: forbidden}
	if c.Image == "" {
		c.Image = DefaultExecutorImage
	}
	if c.AuthVolume == "" {
		c.AuthVolume = DefaultAuthVolume
	}
	if c.Home == "" {
		c.Home = DefaultContainerHome
	}
	if c.AuthMount == "" {
		c.AuthMount = c.Home + "/.claude"
	}
	if c.Network == "" {
		c.Network = DefaultExecutorNet
	}
	return c
}

// launchRef is what `docker run` is given: the image ID the preflight
// resolved, and only the TAG when there is none (no preflight has run —
// the unshelled path in the tests). A tag is mutable: an image rebuilt or
// retagged between the preflight and the launch would run a world the
// record does not name, which is the one thing this lane exists to prevent
// (review r2). Launching by id makes the record and the call the same
// world by construction, and an id the daemon no longer has fails the
// launch instead of silently substituting another.
func (c *Container) launchRef() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.digest != "" {
		return c.digest
	}
	return c.Image
}

// Executor is what a call through this launcher records. The digest is
// whatever the preflight resolved (empty before the first one, which is why
// the decision always preflights first).
func (c *Container) Executor() Executor {
	c.mu.Lock()
	digest := c.digest
	c.mu.Unlock()
	return Executor{Kind: ExecutorContainer, Image: c.Image, Digest: digest, Network: c.Network}
}

// NamePrefix is what every container this engine starts is named with:
// Python main's executor prefix plus `go-`. Main's stranded-container sweep
// filters `docker ps` by the `maro.owner_pid` LABEL **and** the
// `maro-exec-` name prefix, and kills only containers whose owner process
// is dead (src/container_exec.py:sweep_stranded_containers). Sharing the
// prefix means the sweep that already runs on this box reaps this engine's
// leftovers too — a live container is protected by its owner being alive,
// so the coupling costs nothing and a container left behind by a killed
// engine stops being nobody's job.
const NamePrefix = "maro-exec-go-"

// name is this call's container name: the prefix, the engine's pid and a
// counter, so two calls of one engine never collide and a stranded
// container says whose it was.
func (c *Container) name() string {
	return fmt.Sprintf("%s%d-%d", NamePrefix, c.owner(), c.nameN.Add(1))
}

func (c *Container) owner() int {
	if c.Owner != 0 {
		return c.Owner
	}
	return os.Getpid()
}

// authToken reports whether this process holds the CLI token AuthEnv names.
func (c *Container) authToken() bool {
	return c.AuthEnv != "" && os.Getenv(c.AuthEnv) != ""
}

func (c *Container) docker() string {
	if c.Docker == "" {
		return "docker"
	}
	return c.Docker
}

func (c *Container) Wrap(l Launch) (Launched, error) {
	if c.Image == "" {
		return Launched{}, fmt.Errorf("%w: no executor image", ErrExecutorUnavailable)
	}
	// -i: the prompt arrives on stdin. --init: a real pid 1, so a tool's
	// grandchildren are reaped and a signal reaches them. --rm: nothing is
	// left behind on a normal exit. --user: files the worker writes in the
	// work dir stay the operator's, and the auth volume's files (written by
	// the operator's own login, as the same uid) stay readable.
	name := c.name()
	argv := []string{c.docker(), "run", "--rm", "-i", "--init", "--name", name}
	if os.Getuid() >= 0 {
		argv = append(argv, "--user", strconv.Itoa(os.Getuid())+":"+strconv.Itoa(os.Getgid()))
	}
	argv = append(argv, "--label", "maro.owner_pid="+strconv.Itoa(c.owner()))
	bound := map[string]bool{}
	// --mount, not -v: a host path may legally contain a colon. The SOURCE
	// is the resolved path (a symlinked spelling cannot smuggle a forbidden
	// root past the check) and the TARGET is the path the worker was given,
	// so what the run's own record says is what the worker sees.
	if l.Cwd != "" {
		src, err := c.bindSource(l.Cwd, "work dir")
		if err != nil {
			return Launched{}, err
		}
		argv = append(argv, "--mount", "type=bind,source="+src+",target="+l.Cwd)
		bound[src] = true
	}
	for _, f := range l.Files {
		if f == "" {
			continue
		}
		src, err := c.bindSource(f, "hand-off path")
		if err != nil {
			return Launched{}, err
		}
		argv = append(argv, "--mount", "type=bind,source="+src+",target="+f+",readonly")
	}
	for _, w := range l.Writable {
		if w == "" {
			continue
		}
		if !filepath.IsAbs(w) {
			return Launched{}, fmt.Errorf("%w: writable path %q is not absolute", ErrExecutorUnavailable, w)
		}
		dir, err := writableDir(w)
		if err != nil {
			return Launched{}, err
		}
		src, err := c.bindSource(dir, "writable path")
		if err != nil {
			return Launched{}, err
		}
		if bound[src] || l.Cwd != "" && under(dir, l.Cwd) {
			continue // already writable through a bind this call already has
		}
		bound[src] = true
		argv = append(argv, "--mount", "type=bind,source="+src+",target="+dir)
	}
	if c.AuthVolume != "" {
		argv = append(argv, "--mount", "type=volume,source="+c.AuthVolume+",target="+c.AuthMount)
	}
	if c.Home != "" {
		argv = append(argv, "-e", "HOME="+c.Home)
	}
	if c.Network != "" {
		argv = append(argv, "--network", c.Network)
	}
	// Names on the command line, values in THIS process's environment:
	// docker copies each named variable from its own client env, so no
	// secret value is ever visible in a host process listing.
	env := os.Environ()
	if c.authToken() {
		argv = append(argv, "-e", c.AuthEnv) // the value is already in env
	}
	for _, kv := range l.Env {
		name := kv
		if i := strings.IndexByte(kv, '='); i >= 0 {
			name = kv[:i]
		}
		if name == "" {
			continue
		}
		argv = append(argv, "-e", name)
		env = append(env, kv)
	}
	if l.Cwd != "" {
		argv = append(argv, "-w", l.Cwd)
	}
	argv = append(argv, c.launchRef())
	// The image's own CLI, by name: the host path this process resolved
	// does not exist inside the image.
	argv = append(argv, filepath.Base(l.Bin))
	argv = append(argv, l.Args...)
	return Launched{Argv: argv, Env: env, Stop: func(ctx context.Context) error { return c.kill(ctx, name) }}, nil
}

// bindSource is the host path to bind for p: resolved through symlinks (it
// must exist), refused when it IS or CONTAINS a Forbidden root, and refused
// when it is at or inside a Sealed one. A DESCENDANT of a forbidden root is
// fine and is the normal case — the drop directory lives inside the
// workspace, and binding it is the point; a descendant of a SEALED root
// never is.
func (c *Container) bindSource(p, what string) (string, error) {
	// Absolute first: the TARGET inside the container is the path the run's
	// own record names, and a relative one names nothing there. Resolution
	// would silently succeed against this process's cwd (review r2 — the
	// only relative fixture was a path that did not exist, so the refusal
	// came from the wrong place).
	if !filepath.IsAbs(p) {
		return "", fmt.Errorf("%w: %s %s is not an absolute path: a container sees the path the record names", ErrExecutorUnavailable, what, p)
	}
	src, err := filepath.EvalSymlinks(p)
	if err != nil {
		return "", fmt.Errorf("%w: %s %s cannot be resolved: %v", ErrExecutorUnavailable, what, p, err)
	}
	for _, f := range c.Forbidden {
		if f == "" {
			continue
		}
		root, rerr := filepath.EvalSymlinks(f)
		if rerr != nil {
			root = filepath.Clean(f)
		}
		if under(root, src) {
			return "", fmt.Errorf("%w: %s %s is or contains %s, which is never mounted into a container: work in a directory under it instead", ErrExecutorUnavailable, what, p, root)
		}
	}
	for _, f := range c.Sealed {
		if f == "" {
			continue
		}
		root, rerr := filepath.EvalSymlinks(f)
		if rerr != nil {
			root = filepath.Clean(f)
		}
		// A root that is not absolute cannot be compared with a resolved
		// bind source at all: `filepath.Rel` refuses the mixed pair and the
		// containment silently reads as "no". A protection this process
		// cannot evaluate is a protection it does not have, so the bind is
		// refused rather than allowed (review r3 — a relative
		// MARO_SECRETS_DIR walked straight through).
		if !filepath.IsAbs(root) {
			return "", fmt.Errorf("%w: %s %s cannot be checked against %s, which is not an absolute path: nothing is bound while a protected root is unresolved", ErrExecutorUnavailable, what, p, f)
		}
		if under(src, root) || under(root, src) {
			return "", fmt.Errorf("%w: %s %s is inside %s, which no container ever sees: work somewhere else", ErrExecutorUnavailable, what, p, root)
		}
	}
	return src, nil
}

// writableDir is the DIRECTORY to bind so the child can write path p: p
// itself when it is an existing directory, otherwise its parent when that
// exists. Never a single-file bind — a file bind detaches the moment the
// writer does the atomic write-and-rename every careful writer does, and
// the host would keep the empty file the engine created (Python main paid
// for this one on the fence mounts). Never a missing directory either:
// docker would create it, root-owned, and the run would fail on a
// permission error far from here.
func writableDir(p string) (string, error) {
	if st, err := os.Stat(p); err == nil && st.IsDir() {
		return p, nil
	}
	parent := filepath.Dir(p)
	if st, err := os.Stat(parent); err == nil && st.IsDir() {
		return parent, nil
	}
	return "", fmt.Errorf("%w: %s has no directory on this host to write into: create it and resume", ErrExecutorUnavailable, p)
}

// under says whether dir is at or inside root (both absolute, cleaned).
func under(dir, root string) bool {
	rel, err := filepath.Rel(root, dir)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

// kill ends the container this call started. Best-effort by construction:
// `--rm` may have removed it already, the daemon may be down, and either
// way there is nothing left to end. This is the timeout path's other half
// (see Launched.Stop).
func (c *Container) kill(ctx context.Context, name string) error {
	if name == "" {
		return nil
	}
	cctx, cancel := context.WithTimeout(ctx, probeTimeout)
	defer cancel()
	out, err := c.run(cctx, []string{c.docker(), "kill", name})
	if err == nil || gone(out) {
		return nil // confirmed absence IS the outcome this asked for
	}
	if msg := strings.TrimSpace(string(out)); msg != "" {
		return fmt.Errorf("%s: %w", msg, err)
	}
	return err
}

// gone: docker's way of saying there is nothing left to end. The normal
// case, because `--rm` removes a container the moment it exits — which is
// why the stop runs on EVERY return and treats this as success rather than
// guessing from the call's outcome whether the container is still there
// (review r2: an independently killed client, and a panic, both leave a
// live container behind a context that was never cancelled).
func gone(out []byte) bool {
	s := strings.ToLower(string(out))
	// "no such container" and docker's own "cannot kill … is not running".
	// NOT a bare "is not running": a daemon that says IT is not running is
	// a failure to end the container, not a container that ended (review
	// r3).
	return strings.Contains(s, "no such container") ||
		strings.Contains(s, "cannot kill container") && strings.Contains(s, "not running")
}

// Preflight: the daemon answers, the image is here (and its digest is the
// world the record will name), the auth volume is here and holds a login.
//
// The DAEMON is probed on every call, never remembered: docker comes and
// goes, and a remembered yes would let `on` record a container for a call
// that could not start and `require` dispatch one that cannot run (main
// probes fresh per call for the same reason). The image, the volume and the
// login are remembered once verified — they do not come and go — and a
// FAILURE of any of them is never remembered, so an operator who fixes it
// mid-run is picked up by the next call without a restart.
func (c *Container) Preflight(ctx context.Context) error {
	dctx, dcancel := context.WithTimeout(ctx, probeTimeout)
	_, derr := c.run(dctx, []string{c.docker(), "version", "--format", "{{.Server.Version}}"})
	dcancel()
	if derr != nil {
		return fmt.Errorf("%w: the docker daemon does not answer: start docker and resume", ErrExecutorUnavailable)
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.ready {
		return nil
	}
	ictx, icancel := context.WithTimeout(ctx, probeTimeout)
	out, ierr := c.run(ictx, []string{c.docker(), "image", "inspect", "--format", "{{.Id}}", c.Image})
	icancel()
	if ierr != nil {
		return fmt.Errorf("%w: executor image %s is not here: build it (deploy/docker/Dockerfile.executor) or name another with --executor-image, and resume", ErrExecutorUnavailable, c.Image)
	}
	digest := strings.TrimSpace(string(out))
	// An image id the daemon did not actually give us is worse than none:
	// the record would name a world nobody can look up, and under `require`
	// the fold would accept it. Fail closed instead (review r2).
	if !isImageID(digest) {
		return fmt.Errorf("%w: the docker daemon gave no usable image id for %s (%q): check the daemon and resume", ErrExecutorUnavailable, c.Image, truncateRef(digest))
	}
	// A token in hand is the login: the volume is only the CLI's state
	// directory then, and docker creates a missing one on first use.
	if c.AuthVolume != "" && !c.authToken() {
		vctx, vcancel := context.WithTimeout(ctx, probeTimeout)
		_, verr := c.run(vctx, []string{c.docker(), "volume", "inspect", c.AuthVolume})
		vcancel()
		if verr != nil {
			return fmt.Errorf("%w: auth volume %s is not here: seed it with the container login (maro-bootstrap container-setup) and resume", ErrExecutorUnavailable, c.AuthVolume)
		}
		// A volume that exists and is EMPTY is the "installed but not
		// logged in" trap: the call would start and fail as an ordinary CLI
		// error, and a `require` attempt would retry into the same wall.
		// One container start, once per process, asks whether the CLI's own
		// credentials file is a regular file, readable AS THE EXECUTOR UID,
		// and shaped like the JSON object it is supposed to be — never its
		// contents. (Whether the session inside has EXPIRED is main's
		// breaker's question, over cached observations; this catches the
		// common operator errors.)
		name := c.name()
		lctx, lcancel := context.WithTimeout(ctx, loginTimeout)
		// the id, not the tag: a retag between the inspect and here would
		// otherwise have the probe prove a login in a different image from
		// the one the worker runs (review r3). c.digest is not read here —
		// the lock is already held — so the local value is passed in.
		_, lerr := c.run(lctx, append(c.probeRun(name, digest), "-c", loginScript(c.AuthMount)))
		lcancel()
		if lerr != nil {
			// The probe is a container like any other, so it is ended like
			// one: a stalled start killed only the CLIENT, and the probe
			// container would have outlived a degrade to the host (review
			// r2). Its name carries the sweep's prefix either way.
			if kerr := c.kill(context.WithoutCancel(ctx), name); kerr != nil {
				return fmt.Errorf("%w: the container login probe could not be ended (%v) and the volume %s could not be checked: check docker and resume", ErrExecutorUnavailable, kerr, c.AuthVolume)
			}
			return fmt.Errorf("%w: auth volume %s holds no usable container login: run the login step (maro-bootstrap container-setup) and resume", ErrExecutorUnavailable, c.AuthVolume)
		}
	}
	c.digest, c.ready = digest, true
	return nil
}

// probeRun is the docker argv for a throwaway container over the auth
// volume only: the same uid and HOME the real call uses (a probe as root
// would validate an identity the executor never runs as — main's 2026-07-12
// finding), the volume read-only, no network, no work dir. It is NAMED and
// LABELLED like a worker container, because it is one: the engine must be
// able to end it, and the sweep must be able to recognise it (review r2).
func (c *Container) probeRun(name, ref string) []string {
	if ref == "" {
		ref = c.Image
	}
	argv := []string{c.docker(), "run", "--rm", "--network", "none", "--name", name, "--label", "maro.owner_pid=" + strconv.Itoa(c.owner())}
	if os.Getuid() >= 0 {
		argv = append(argv, "--user", strconv.Itoa(os.Getuid())+":"+strconv.Itoa(os.Getgid()))
	}
	if c.Home != "" {
		argv = append(argv, "-e", "HOME="+c.Home)
	}
	if c.AuthVolume != "" {
		argv = append(argv, "--mount", "type=volume,source="+c.AuthVolume+",target="+c.AuthMount+",readonly")
	}
	argv = append(argv, "--entrypoint", "python3", ref)
	return argv
}

// loginScript is what the probe asks inside the image: is the CLI's
// credentials file a readable JSON object of the CLI's own SHAPE — a
// `claudeAiOauth` object holding a non-empty refresh token?
//
// Two weaker versions came before it. `test -s` passes on a directory named
// .credentials.json and on any non-empty garbage (review r2). A first-byte
// check passes on `{}` and on unrelated JSON (review r3). Main asks exactly
// this question inside the same image with python3
// (`src/container_exec.py:_credentials_expiry_probe`), so the shape is the
// one the two engines already agree on and the interpreter is known to be
// there.
//
// It never prints the file, and it exits with a fixed message. It does NOT
// prove the session is unexpired — that is a live question, answered by the
// call itself and by main's breaker over cached observations.
func loginScript(mount string) string {
	return "import json,sys\n" +
		"p=" + strconv.Quote(mount+"/.credentials.json") + "\n" +
		"try:\n" +
		"    d=json.load(open(p)); o=d.get('claudeAiOauth') if isinstance(d,dict) else None\n" +
		"except Exception:\n" +
		"    o=None\n" +
		"t=o.get('refreshToken') if isinstance(o,dict) else None\n" +
		"if not isinstance(t,str) or not t.strip(): sys.exit('credentials file missing, unreadable or not the CLI shape')\n"
}

// isImageID: a content-addressed image id, `<algorithm>:<hex>`. "Not empty
// and no quotes in it" was not enough — a TAG passes that, and a tag
// recorded as a digest and handed to `docker run` is the mutable reference
// this lane exists to stop naming (review r3). The algorithm is not pinned
// to sha256: the point is that the reference names contents, and a daemon
// that answers with anything else refuses the call loudly instead of
// recording something unusable.
func isImageID(s string) bool {
	alg, hex, ok := strings.Cut(s, ":")
	if !ok || len(alg) < 2 || len(alg) > 32 || len(hex) < 32 || len(hex) > 128 {
		return false
	}
	for _, r := range alg {
		if !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '+' || r == '.' || r == '-') {
			return false
		}
	}
	for _, r := range hex {
		if !(r >= '0' && r <= '9' || r >= 'a' && r <= 'f') {
			return false
		}
	}
	return true
}

// truncateRef keeps an unusable daemon answer short enough to read in a
// refusal without pasting an unbounded blob into the operator's terminal.
func truncateRef(s string) string {
	if len(s) > 80 {
		return s[:80] + "…"
	}
	return s
}

// run is the docker call seam: c.Exec when the caller gave one, the real
// thing otherwise.
func (c *Container) run(ctx context.Context, argv []string) ([]byte, error) {
	if c.Exec != nil {
		return c.Exec(ctx, argv)
	}
	return realExec(ctx, argv)
}

func realExec(ctx context.Context, argv []string) ([]byte, error) {
	if _, err := exec.LookPath(argv[0]); err != nil {
		return nil, err
	}
	return exec.CommandContext(ctx, argv[0], argv[1:]...).CombinedOutput()
}
