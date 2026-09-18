package invoke

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// executored is a backend that answers for an executor, and can refuse.
type executored struct {
	Scripted
	ex  Executor
	err error
}

func (e *executored) ExecutorFor(context.Context, Request) (Executor, error) {
	return e.ex, e.err
}

// Where a call runs is committed WITH the invocation, before dispatch: the
// record and the call cannot disagree. A backend that refuses (the required
// executor cannot run) leaves NOTHING in the journal — no invocation, no
// dispatch, no call.
// testID is a real-shaped image id: the preflight refuses anything that is
// not `<algorithm>:<hex>`, because a TAG recorded as a digest is the mutable
// reference this lane exists to stop naming (review r3).
const testID = "sha256:bebebebebebebebebebebebebebebebebebebebebebebebebebebebebebebebe"

func TestExecutorLaneIsRecordedBeforeDispatch(t *testing.T) {
	image := "maro-executor:2.1.210-r3"
	t.Run("recorded", func(t *testing.T) {
		sh, _ := newShell(t)
		b := &executored{Scripted: Scripted{Caps: Capabilities{Name: "b", Model: "m", ActsOutward: true}, Calls: []ScriptedCall{{Response: []byte("ok")}}}, ex: Executor{Kind: ExecutorContainer, Image: image}}
		out, err := sh.Invoke(ctxBg, b, Request{Purpose: PurposeExecute, Prompt: []byte("go"), Tools: true}, nil)
		if err != nil {
			t.Fatal(err)
		}
		var inv *Invocation
		sh.J.Production().Scan(0, func(r record.Record) error {
			if x, ok := r.(*Invocation); ok && x.ID == out.Invocation {
				inv = x
			}
			return nil
		})
		if inv == nil || inv.Executor == nil {
			t.Fatalf("invocation %+v", inv)
		}
		if inv.Executor.Kind != ExecutorContainer || inv.Executor.Image != image {
			t.Fatalf("recorded executor %+v", inv.Executor)
		}
	})
	t.Run("a backend that answers for none records none", func(t *testing.T) {
		sh, _ := newShell(t)
		b := &Scripted{Caps: Capabilities{Name: "b", Model: "m"}, Calls: []ScriptedCall{{Response: []byte("ok")}}}
		out, err := sh.Invoke(ctxBg, b, Request{Purpose: PurposeJudge, Prompt: []byte("?")}, nil)
		if err != nil {
			t.Fatal(err)
		}
		var inv *Invocation
		sh.J.Production().Scan(0, func(r record.Record) error {
			if x, ok := r.(*Invocation); ok && x.ID == out.Invocation {
				inv = x
			}
			return nil
		})
		if inv == nil || inv.Executor != nil {
			t.Fatalf("executor on a backend that answers for none: %+v", inv.Executor)
		}
	})
	t.Run("a refusal leaves nothing", func(t *testing.T) {
		sh, _ := newShell(t)
		want := fmt.Errorf("%w: %w", ErrBeforeDispatch, fmt.Errorf("%w: no image and resume", ErrExecutorUnavailable))
		b := &executored{Scripted: Scripted{Caps: Capabilities{Name: "b", Model: "m", ActsOutward: true}, Calls: []ScriptedCall{{Response: []byte("must not run")}}}, err: want}
		_, err := sh.Invoke(ctxBg, b, Request{Purpose: PurposeExecute, Prompt: []byte("go"), Tools: true}, nil)
		if !errors.Is(err, ErrExecutorUnavailable) || !errors.Is(err, ErrBeforeDispatch) {
			t.Fatalf("refusal: %v", err)
		}
		n := 0
		sh.J.Production().Scan(0, func(r record.Record) error {
			switch r.(type) {
			case *Invocation, *Dispatched:
				n++
			}
			return nil
		})
		if n != 0 || len(b.Seen) != 0 {
			t.Fatalf("records=%d calls=%d after a refusal", n, len(b.Seen))
		}
	})
	t.Run("a backend that answers with a lie is a contract violation", func(t *testing.T) {
		sh, _ := newShell(t)
		b := &executored{Scripted: Scripted{Caps: Capabilities{Name: "b", Model: "m", ActsOutward: true}, Calls: []ScriptedCall{{Response: []byte("x")}}}, ex: Executor{Kind: ExecutorContainer}}
		if _, err := sh.Invoke(ctxBg, b, Request{Purpose: PurposeExecute, Prompt: []byte("go"), Tools: true}, nil); !errors.Is(err, ErrBackendContract) {
			t.Fatalf("container with no image: %v", err)
		}
	})
	t.Run("the door: a tool-less call is never in a container", func(t *testing.T) {
		// The rule is the DOOR's, not an attempt policy's: it holds for
		// every invocation this engine will ever read, including the
		// landscape's, which is attempt 0 and reaches no attempt's checks
		// at all (review r1).
		inv := &Invocation{Header: record.Header{ID: record.NewID(), Schema: "invocation/1", RunID: "r", Attempt: 1, Subject: record.Ref{Kind: "prompt", ID: "x"}, At: time.Now().UTC()},
			Purpose: PurposeJudge, Request: thought.Ref{Hash: "s256v1:" + strings.Repeat("ab", 32), Kind: thought.Prompt, Bytes: 1, Encoding: thought.UTF8}, Backend: Capabilities{Name: "b"}, EffectToken: strings.Repeat("ab", 16)}
		for _, c := range []struct {
			name    string
			purpose Purpose
			tools   bool
			attempt uint32
			ex      *Executor
			want    string
		}{
			{"a judge in a container", PurposeJudge, false, 1, &Executor{Kind: ExecutorContainer, Image: "i"}, "never asks for"},
			// attempt 0 for real: the landscape call belongs to no attempt,
			// which is why this rule cannot live in an attempt's checks
			{"the landscape in a container", PurposeLandscape, false, 0, &Executor{Kind: ExecutorContainer, Image: "i"}, "never asks for"},
			{"a judge on the host", PurposeJudge, false, 1, &Executor{Kind: ExecutorHost}, ""},
			{"a judge that says nothing", PurposeJudge, false, 1, nil, ""},
			{"the landscape on the host", PurposeLandscape, false, 0, &Executor{Kind: ExecutorHost}, ""},
			{"an intent read in a container", PurposeIntent, false, 1, &Executor{Kind: ExecutorContainer, Image: "i"}, "never asks for"},
			{"an execute in a container", PurposeExecute, true, 1, &Executor{Kind: ExecutorContainer, Image: "i"}, ""},
		} {
			inv.Purpose, inv.Tools, inv.Executor, inv.Attempt = c.purpose, c.tools, c.ex, c.attempt
			err := inv.ValidateWire()
			if c.want == "" && err != nil {
				t.Fatalf("%s: %v", c.name, err)
			}
			if c.want != "" && (err == nil || !strings.Contains(err.Error(), c.want)) {
				t.Fatalf("%s: %v (want %q)", c.name, err, c.want)
			}
		}
	})
	t.Run("the door", func(t *testing.T) {
		for _, c := range []struct {
			name string
			ex   Executor
			want string
		}{
			{"host", Executor{Kind: ExecutorHost}, ""},
			{"container", Executor{Kind: ExecutorContainer, Image: "i"}, ""},
			{"out of vocabulary", Executor{Kind: "vm"}, "out of vocabulary"},
			{"container with no image", Executor{Kind: ExecutorContainer}, "no image"},
			{"host naming an image", Executor{Kind: ExecutorHost, Image: "i"}, "naming image"},
			{"host naming a digest", Executor{Kind: ExecutorHost, Digest: "sha256:x"}, "naming digest"},
			{"host naming a network", Executor{Kind: ExecutorHost, Network: "bridge"}, "naming network"},
			{"container with digest and network", Executor{Kind: ExecutorContainer, Image: "i", Digest: "sha256:x", Network: "none"}, ""},
		} {
			err := c.ex.validate()
			if c.want == "" && err != nil {
				t.Fatalf("%s: %v", c.name, err)
			}
			if c.want != "" && (err == nil || !strings.Contains(err.Error(), c.want)) {
				t.Fatalf("%s: %v (want %q)", c.name, err, c.want)
			}
		}
	})
}

// The container's argv: the work dir bound at the SAME path (so a path in
// the run's own record means one thing in both worlds), the secrets
// hand-off read-only, the auth volume and HOME, the image's own CLI by
// name — and secret VALUES in this process's environment, never in an argv
// a host process listing can read.
func TestContainerWrapsTheSameCall(t *testing.T) {
	dir := t.TempDir()
	// the hand-off exists by the time a call is wrapped (HandOff.write runs
	// first), and a bind source that does not exist is refused
	handoff := filepath.Join(dir, "secrets.env")
	if err := os.WriteFile(handoff, []byte("NAME=v\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	// the drop directory the worker writes its question and its derived
	// secrets into: a sibling of the work dir, so it needs its own bind
	dropDir := t.TempDir()
	ask := filepath.Join(dropDir, "ask-operator.json")
	forbidden := t.TempDir() // stands in for the workspace root: never bound
	c := NewContainer("img:1", "vol", "/home/maro/.claude", "/home/maro", "none", forbidden)
	lch, err := c.Wrap(Launch{Bin: "/opt/homebrew/bin/claude", Args: []string{"-p", "--model", "sonnet"}, Cwd: dir, Env: []string{"YAHOO_APP_PASSWORD=hunter2", "MARO_SECRETS=" + handoff}, Files: []string{handoff}, Writable: []string{ask, filepath.Join(dir, "in-the-work-dir.json")}})
	if err != nil {
		t.Fatal(err)
	}
	argv, wd, env := lch.Argv, lch.Dir, lch.Env
	line := strings.Join(argv, " ")
	for _, want := range []string{
		"docker run --rm -i --init --name " + NamePrefix,
		"--user " + strconv.Itoa(os.Getuid()) + ":" + strconv.Itoa(os.Getgid()),
		"--label maro.owner_pid=" + strconv.Itoa(os.Getpid()),
		"--mount type=bind,source=" + dir + ",target=" + dir,
		"--mount type=bind,source=" + handoff + ",target=" + handoff + ",readonly",
		// the ask file does not exist yet: its DIRECTORY is bound, writable
		"--mount type=bind,source=" + dropDir + ",target=" + dropDir,
		"--mount type=volume,source=vol,target=/home/maro/.claude",
		"-e HOME=/home/maro",
		"--network none",
		"-e YAHOO_APP_PASSWORD",
		"-w " + dir,
		"img:1 claude -p --model sonnet",
	} {
		if !strings.Contains(line, want) {
			t.Fatalf("argv %q\nwants %q", line, want)
		}
	}
	if strings.Contains(line, "hunter2") {
		t.Fatal("a secret VALUE reached the argv")
	}
	if !containsLine(env, "YAHOO_APP_PASSWORD=hunter2") || !containsLine(env, "MARO_SECRETS="+handoff) {
		t.Fatal("the value did not reach the docker client's own environment")
	}
	if wd != "" {
		t.Fatalf("the client runs in %q (the container's -w is the run's dir)", wd)
	}
	if strings.Contains(line, "/opt/homebrew/bin/claude") {
		t.Fatal("the host's CLI path was handed to the image")
	}
	// two calls of one engine never collide
	l2, _ := c.Wrap(Launch{Bin: "claude"})
	if name(argv) == name(l2.Argv) {
		t.Fatalf("both calls named %q", name(argv))
	}
	// the container this call started can be ended: killing the docker
	// CLIENT leaves it running, so the call carries the way to end the work
	if lch.Stop == nil {
		t.Fatal("no way to end the container this call started")
	}
	killed := []string{}
	c.Exec = func(_ context.Context, argv []string) ([]byte, error) {
		killed = append(killed, strings.Join(argv, " "))
		return nil, nil
	}
	if err := lch.Stop(ctxBg); err != nil {
		t.Fatal(err)
	}
	if len(killed) != 1 || !strings.HasSuffix(killed[0], "kill "+name(argv)) {
		t.Fatalf("stop ran %v", killed)
	}
	c.Exec = nil
	// a relative path is refused rather than mounted somewhere surprising
	if _, err := c.Wrap(Launch{Bin: "claude", Cwd: "work"}); !errors.Is(err, ErrExecutorUnavailable) {
		t.Fatalf("relative work dir: %v", err)
	}
	if _, err := c.Wrap(Launch{Bin: "claude", Files: []string{filepath.Join(dir, "never-written.env")}}); !errors.Is(err, ErrExecutorUnavailable) {
		t.Fatalf("a hand-off that was never written: %v", err)
	}
	// a writable path already inside the work dir is not bound twice, and
	// nothing is ever bound at file granularity: a single-file bind detaches
	// on the atomic rename a careful writer does, and the worker's question
	// would never leave the container
	if n := strings.Count(line, "--mount type=bind"); n != 3 {
		t.Fatalf("%d bind mounts in %q (work dir, hand-off, drop dir)", n, line)
	}
	// the host lane wraps the same call and has nothing to end
	hl, err := HostLauncher{}.Wrap(Launch{Bin: "/usr/bin/claude", Args: []string{"-p"}, Cwd: dir})
	if err != nil || hl.Argv[0] != "/usr/bin/claude" || hl.Dir != dir || hl.Stop != nil {
		t.Fatalf("host launch %+v %v", hl, err)
	}
	if strings.Contains(line, "target="+ask) {
		t.Fatalf("the ask FILE was bound: %q", line)
	}
	// a writable path with no directory on this host to live in is refused
	// before dispatch, naming what to create
	if _, err := c.Wrap(Launch{Bin: "claude", Writable: []string{filepath.Join(dir, "no", "such", "dir", "ask.json")}}); !errors.Is(err, ErrExecutorUnavailable) {
		t.Fatalf("missing writable home: %v", err)
	}
	if _, err := c.Wrap(Launch{Bin: "claude", Writable: []string{"drop/ask.json"}}); !errors.Is(err, ErrExecutorUnavailable) {
		t.Fatalf("relative writable: %v", err)
	}
	// An EXISTING relative path is refused too, and not because resolution
	// failed: the target inside the container must be the path the run's own
	// record names, and a relative one names nothing (review r2 — the only
	// relative fixture was a path that did not exist, so it proved the
	// wrong thing).
	rel := "relative-" + strconv.Itoa(os.Getpid())
	if err := os.MkdirAll(rel, 0o700); err == nil {
		defer os.RemoveAll(rel)
		if _, err := c.Wrap(Launch{Bin: "claude", Cwd: rel}); !errors.Is(err, ErrExecutorUnavailable) {
			t.Fatalf("an existing relative work dir: %v", err)
		}
	}
}

// The call runs in the image the record NAMES. The tag is mutable — an
// image rebuilt or retagged between the preflight and the launch is a
// different world under the same name — so the launch is by the id the
// preflight resolved, and a launcher asked to run a venue it does not have
// refuses before dispatch (review r2).
func TestTheLaunchIsByTheIdTheRecordNames(t *testing.T) {
	id := "sha256:" + strings.Repeat("be", 32)
	c := NewContainer("img:1", "", "", "", "none")
	// before any preflight there is no id, so the tag is all there is (the
	// unshelled path: nothing has been recorded either)
	l, err := c.Wrap(Launch{Bin: "claude"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(strings.Join(l.Argv, " "), " img:1 claude") {
		t.Fatalf("argv without a preflight: %v", l.Argv)
	}
	c.Exec = func(context.Context, []string) ([]byte, error) { return []byte(id + "\n"), nil }
	if err := c.Preflight(ctxBg); err != nil {
		t.Fatal(err)
	}
	// the tag now resolves to an id: the record names it AND the call runs it
	ex := c.Executor()
	if ex.Digest != id || ex.Image != "img:1" {
		t.Fatalf("recorded %+v", ex)
	}
	l, err = c.Wrap(Launch{Bin: "claude"})
	if err != nil {
		t.Fatal(err)
	}
	line := strings.Join(l.Argv, " ")
	if !strings.Contains(line, " "+id+" claude") {
		t.Fatalf("the call did not run the id it recorded: %v", l.Argv)
	}
	// and the mutable tag is not what was run: someone retagging img:1
	// between the preflight and here cannot move the call
	if strings.Contains(line, " img:1 ") {
		t.Fatalf("the tag was handed to docker: %v", l.Argv)
	}
}

// A bind source is resolved and then refused when it IS or CONTAINS a root
// that must never reach a container: `--work <workspace>` would otherwise
// hand the journal, the thought store and the secrets drop to the worker
// read-write and still record "container". A DESCENDANT of a forbidden root
// is the normal case and stays allowed — the drop directory lives inside
// the workspace.
func TestContainerRefusesAForbiddenMount(t *testing.T) {
	ws := t.TempDir()
	inside := filepath.Join(ws, "work")
	if err := os.MkdirAll(inside, 0o700); err != nil {
		t.Fatal(err)
	}
	c := NewContainer("img:1", "", "", "", "none", ws)
	if _, err := c.Wrap(Launch{Bin: "claude", Cwd: ws}); !errors.Is(err, ErrExecutorUnavailable) || !strings.Contains(err.Error(), "never mounted") {
		t.Fatalf("the workspace itself was mountable: %v", err)
	}
	above := filepath.Dir(ws)
	if _, err := c.Wrap(Launch{Bin: "claude", Cwd: above}); !errors.Is(err, ErrExecutorUnavailable) {
		t.Fatalf("a directory CONTAINING the workspace was mountable: %v", err)
	}
	if _, err := c.Wrap(Launch{Bin: "claude", Cwd: inside}); err != nil {
		t.Fatalf("a work dir inside the workspace: %v", err)
	}
	// a symlinked spelling resolves to the same place and is refused there
	link := filepath.Join(t.TempDir(), "ws-link")
	if err := os.Symlink(ws, link); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Wrap(Launch{Bin: "claude", Cwd: link}); !errors.Is(err, ErrExecutorUnavailable) {
		t.Fatalf("a symlink to the workspace was mountable: %v", err)
	}
	// and a bind source that does not exist is refused before dispatch
	if _, err := c.Wrap(Launch{Bin: "claude", Cwd: filepath.Join(ws, "gone")}); !errors.Is(err, ErrExecutorUnavailable) {
		t.Fatalf("a missing work dir: %v", err)
	}
	// a symlink whose TARGET is a directory containing the workspace is the
	// same refusal, reached the other way round
	up := filepath.Join(t.TempDir(), "above-link")
	if err := os.Symlink(above, up); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Wrap(Launch{Bin: "claude", Cwd: up}); !errors.Is(err, ErrExecutorUnavailable) {
		t.Fatalf("a symlink to a directory containing the workspace: %v", err)
	}
	// SEALED is the other rule: nothing at or inside it is ever bound. The
	// secrets store is a DESCENDANT of the forbidden ~/.maro, so the
	// contains-rule allowed it — `--work ~/.maro/secrets` would have handed
	// the sops file and the age identity to the worker read-write (r2).
	store := filepath.Join(ws, "secrets")
	deeper := filepath.Join(store, "keys")
	if err := os.MkdirAll(deeper, 0o700); err != nil {
		t.Fatal(err)
	}
	c.Sealed = []string{store}
	for _, p := range []string{store, deeper} {
		if _, err := c.Wrap(Launch{Bin: "claude", Cwd: p}); !errors.Is(err, ErrExecutorUnavailable) || !strings.Contains(err.Error(), "no container ever sees") {
			t.Fatalf("%s was mountable: %v", p, err)
		}
	}
	// a sealed tree is refused as a writable channel and as a hand-off too,
	// not only as the work dir
	if _, err := c.Wrap(Launch{Bin: "claude", Cwd: inside, Writable: []string{filepath.Join(deeper, "x.json")}}); !errors.Is(err, ErrExecutorUnavailable) {
		t.Fatalf("a writable path inside the sealed store: %v", err)
	}
	handoff := filepath.Join(deeper, "secrets.env")
	if err := os.WriteFile(handoff, []byte("N=v\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Wrap(Launch{Bin: "claude", Cwd: inside, Files: []string{handoff}}); !errors.Is(err, ErrExecutorUnavailable) {
		t.Fatalf("a hand-off inside the sealed store: %v", err)
	}
	// A sealed root that is not ABSOLUTE cannot be compared with a resolved
	// bind source at all — filepath.Rel refuses the mixed pair and the
	// containment silently reads "no". So nothing is bound while a protected
	// root is unresolved (review r3: a relative MARO_SECRETS_DIR walked
	// straight through its own check).
	c.Sealed = []string{"private/secrets"}
	if _, err := c.Wrap(Launch{Bin: "claude", Cwd: inside}); !errors.Is(err, ErrExecutorUnavailable) || !strings.Contains(err.Error(), "not an absolute path") {
		t.Fatalf("a relative sealed root: %v", err)
	}
	c.Sealed = nil
	// and a sibling of the sealed tree is still fine
	if _, err := c.Wrap(Launch{Bin: "claude", Cwd: inside}); err != nil {
		t.Fatalf("a work dir beside the sealed store: %v", err)
	}
}

func containsLine(env []string, want string) bool {
	for _, e := range env {
		if e == want {
			return true
		}
	}
	return false
}

func name(argv []string) string {
	for i, a := range argv {
		if a == "--name" && i+1 < len(argv) {
			return argv[i+1]
		}
	}
	return ""
}

// The preflight says what is missing and what to do about it, remembers
// what does not come and go (the image, the volume, the login) and probes
// the DAEMON every time, so an `on` call never records a container it could
// not have started and an operator who fixes docker mid-run is picked up by
// the next call.
func TestContainerPreflightNamesWhatToFix(t *testing.T) {
	for _, c := range []struct {
		fail string
		want string
	}{
		{"version", "docker daemon does not answer"},
		{"image inspect", "executor image img:1 is not here"},
		{"volume inspect", "auth volume vol is not here"},
		{"credentials.json", "holds no usable container login"},
	} {
		n := 0
		ct := NewContainer("img:1", "vol", "", "", "")
		ct.Exec = func(_ context.Context, argv []string) ([]byte, error) {
			n++
			if strings.Contains(strings.Join(argv, " "), c.fail) {
				return nil, errors.New("boom")
			}
			return []byte(testID), nil
		}
		err := ct.Preflight(ctxBg)
		if err == nil || !strings.Contains(err.Error(), c.want) || !strings.Contains(err.Error(), "and resume") {
			t.Fatalf("%s: %v (want %q)", c.fail, err, c.want)
		}
		if !errors.Is(err, ErrExecutorUnavailable) {
			t.Fatalf("%s: not the typed refusal: %v", c.fail, err)
		}
		before := n
		if err := ct.Preflight(ctxBg); err == nil {
			t.Fatalf("%s: a failure was remembered as a success", c.fail)
		}
		if n <= before {
			t.Fatalf("%s: the failure was not probed again (%d)", c.fail, n)
		}
	}
	var seen []string
	ok := NewContainer("img:1", "vol", "", "", "bridge")
	ok.Exec = func(_ context.Context, argv []string) ([]byte, error) {
		seen = append(seen, strings.Join(argv, " "))
		return []byte(" " + testID + "\n"), nil
	}
	if err := ok.Preflight(ctxBg); err != nil {
		t.Fatal(err)
	}
	// the login probe runs as the executor's own uid, over the volume only,
	// read-only and with no network: a probe as root would prove an identity
	// the executor never runs as. It is a container this engine started, so
	// it is NAMED and LABELLED like one — the engine can end it and the
	// sweep can recognise it (review r2) — and it asks for a readable
	// regular file shaped like the JSON object the CLI writes, not merely a
	// non-empty something (`test -s` passes on a directory).
	login := seen[len(seen)-1]
	// the probe runs the PINNED id, never the mutable tag
	if !strings.Contains(login, " python3 "+testID+" ") {
		t.Fatalf("the login probe did not run the resolved id: %q", login)
	}
	for _, want := range []string{"--user " + strconv.Itoa(os.Getuid()), "--network none", ",readonly", "-e HOME=",
		"--name " + NamePrefix, "--label maro.owner_pid=", "--entrypoint python3",
		// the shape, not merely a non-empty file: `claudeAiOauth` holding a
		// non-empty refresh token, which is the question main asks inside
		// the same image (review r2 then r3)
		"claudeAiOauth", "refreshToken", "not the CLI shape"} {
		if !strings.Contains(login, want) {
			t.Fatalf("login probe %q wants %q", login, want)
		}
	}
	// the digest the daemon resolved is what a call records, beside the
	// mutable tag and the network it ran under
	ex := ok.Executor()
	if ex.Digest != testID || ex.Image != "img:1" || ex.Network != "bridge" {
		t.Fatalf("recorded executor %+v", ex)
	}
	ran := len(seen)
	if err := ok.Preflight(ctxBg); err != nil {
		t.Fatal(err)
	}
	// the daemon is asked again (it comes and goes); nothing else is
	if got := len(seen) - ran; got != 1 || !strings.Contains(seen[len(seen)-1], "version") {
		t.Fatalf("second preflight ran %d probe(s): %v", got, seen[ran:])
	}
	// and when the daemon goes away, a remembered success does not cover it
	ok.Exec = func(context.Context, []string) ([]byte, error) { return nil, errors.New("daemon gone") }
	if err := ok.Preflight(ctxBg); err == nil || !strings.Contains(err.Error(), "does not answer") {
		t.Fatalf("a dead daemon behind a cached success: %v", err)
	}
	// An image id the daemon did not really give us is refused, not
	// recorded: the record would name a world nobody can look up, and the
	// fold would accept it (review r2).
	for _, bad := range []string{"", "   ", "sha256:be ef", "no such image\n" + testID,
		"maro-executor:replacement", // a TAG is not an id (review r3)
		"garbage", "sha256:beef", "sha256:" + strings.Repeat("z", 64), ":" + strings.Repeat("ab", 32)} {
		b := bad
		bd := NewContainer("img:1", "", "", "", "")
		bd.Exec = func(_ context.Context, argv []string) ([]byte, error) {
			if strings.Contains(strings.Join(argv, " "), "image inspect") {
				return []byte(b), nil
			}
			return []byte("ok"), nil
		}
		err := bd.Preflight(ctxBg)
		if err == nil || !strings.Contains(err.Error(), "no usable image id") {
			t.Fatalf("digest %q: %v", b, err)
		}
		if ex := bd.Executor(); ex.Digest != "" {
			t.Fatalf("digest %q was recorded anyway: %+v", b, ex)
		}
	}
	// A login probe that stalls leaves a container the engine must end: the
	// name it was given is the name that is killed.
	var killed []string
	st := NewContainer("img:1", "vol", "", "", "")
	st.Exec = func(_ context.Context, argv []string) ([]byte, error) {
		line := strings.Join(argv, " ")
		switch {
		case strings.Contains(line, "credentials.json"):
			return nil, errors.New("deadline exceeded")
		case strings.Contains(line, " kill "):
			killed = append(killed, argv[len(argv)-1])
		}
		return []byte("sha256:" + strings.Repeat("be", 32)), nil
	}
	if err := st.Preflight(ctxBg); err == nil || !strings.Contains(err.Error(), "holds no usable container login") {
		t.Fatalf("stalled login probe: %v", err)
	}
	if len(killed) != 1 || !strings.HasPrefix(killed[0], NamePrefix) {
		t.Fatalf("the probe container was not ended: %v", killed)
	}
}

// Two calls preflighting at once is not a data race and does not run the
// probe sequence twice (run with -race).
func TestContainerPreflightIsSerialized(t *testing.T) {
	var mu sync.Mutex
	n := 0
	ct := NewContainer("img:1", "vol", "", "", "")
	ct.Exec = func(_ context.Context, argv []string) ([]byte, error) {
		mu.Lock()
		defer mu.Unlock()
		if !strings.Contains(strings.Join(argv, " "), "version") {
			n++
		}
		return []byte(testID), nil
	}
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := ct.Preflight(ctxBg); err != nil {
				t.Error(err)
			}
			_ = ct.Executor()
		}()
	}
	wg.Wait()
	if n != 3 {
		t.Fatalf("%d non-daemon probes across 8 concurrent preflights", n)
	}
}

// The policy routes a call: a tool-less one never pays for a container, an
// `on` lane degrades to the host and SAYS SO once per reason, and a
// `require` lane refuses before dispatch rather than running on the host.
func TestExecutorPolicyRoutesTheCall(t *testing.T) {
	newSub := func(pol ExecutorPolicy, probeErr error) (*Subprocess, *[]string) {
		c := NewContainer("img:1", "vol", "", "", "")
		c.Exec = func(context.Context, []string) ([]byte, error) { return []byte(testID), probeErr }
		notes := &[]string{}
		s := &Subprocess{Bin: "/usr/bin/claude", Model: "m", Container: c, Isolation: pol}
		s.Notify = func(r string) { *notes = append(*notes, r) }
		return s, notes
	}
	tools := Request{Purpose: PurposeExecute, Tools: true}
	judge := Request{Purpose: PurposeJudge}
	for _, c := range []struct {
		name  string
		pol   ExecutorPolicy
		probe error
		req   Request
		kind  ExecutorKind
		err   bool
		notes int
	}{
		{"require, container up", ExecutorRequire, nil, tools, ExecutorContainer, false, 0},
		{"require, container down", ExecutorRequire, errors.New("no daemon"), tools, "", true, 0},
		{"on, container up", ExecutorOn, nil, tools, ExecutorContainer, false, 0},
		{"on, container down", ExecutorOn, errors.New("no daemon"), tools, ExecutorHost, false, 1},
		{"off", ExecutorOff, nil, tools, ExecutorHost, false, 0},
		{"unset", "", nil, tools, ExecutorHost, false, 0},
		{"require, tool-less", ExecutorRequire, nil, judge, ExecutorHost, false, 0},
		{"on, tool-less", ExecutorOn, nil, judge, ExecutorHost, false, 0},
	} {
		s, notes := newSub(c.pol, c.probe)
		ex, err := s.ExecutorFor(ctxBg, c.req)
		if c.err {
			if !errors.Is(err, ErrExecutorUnavailable) || !errors.Is(err, ErrBeforeDispatch) {
				t.Fatalf("%s: %v", c.name, err)
			}
			continue
		}
		if err != nil || ex.Kind != c.kind {
			t.Fatalf("%s: %+v %v", c.name, ex, err)
		}
		if len(*notes) != c.notes {
			t.Fatalf("%s: %d degrade notes %v", c.name, len(*notes), *notes)
		}
		// the same reason is announced once, not per call
		s.ExecutorFor(ctxBg, c.req)
		if len(*notes) != c.notes {
			t.Fatalf("%s: the same degrade was announced twice", c.name)
		}
	}
	// `require` with nothing to require refuses before dispatch rather than
	// running on the host and leaving the fold to refuse the record
	bare := &Subprocess{Bin: "/usr/bin/claude", Model: "m", Isolation: ExecutorRequire}
	if _, err := bare.ExecutorFor(ctxBg, tools); !errors.Is(err, ErrBeforeDispatch) || !errors.Is(err, ErrExecutorUnavailable) {
		t.Fatalf("require with no container: %v", err)
	}
	// under `on` it is a degrade, said once
	onNotes := []string{}
	onBare := &Subprocess{Bin: "/usr/bin/claude", Model: "m", Isolation: ExecutorOn, Notify: func(n string) { onNotes = append(onNotes, n) }}
	ex, err := onBare.ExecutorFor(ctxBg, tools)
	if err != nil || ex.Kind != ExecutorHost || len(onNotes) != 1 {
		t.Fatalf("on with no container: %+v %v %v", ex, err, onNotes)
	}
	// the policy this backend enforces is what an attempt records: one owner
	if onBare.ExecutorPolicy() != ExecutorOn {
		t.Fatalf("the backend disowns its policy: %q", onBare.ExecutorPolicy())
	}
}

// The venue is decided ONCE. The shell asks before it commits the
// invocation and the answer rides the request; a call whose venue was
// committed does not decide again, so a probe that starts failing (or
// starts working) between the record and the dispatch cannot move the call
// away from what the journal says.
func TestTheCommittedVenueIsWhereTheCallRuns(t *testing.T) {
	probes := 0
	c := NewContainer("img:1", "vol", "", "", "")
	c.Exec = func(context.Context, []string) ([]byte, error) {
		probes++
		return []byte("sha256:" + strings.Repeat("be", 32)), nil
	}
	s := &Subprocess{Bin: "/usr/bin/claude", Model: "m", Container: c, Isolation: ExecutorOn}
	// committed to the host (the probe was failing when the shell asked):
	// the call runs on the host, and nothing is probed again
	host := Request{Purpose: PurposeExecute, Tools: true, Executor: &Executor{Kind: ExecutorHost}}
	l, err := s.launcher(ctxBg, host)
	if err != nil {
		t.Fatal(err)
	}
	if l.Executor().Kind != ExecutorHost || probes != 0 {
		t.Fatalf("a committed host call resolved to %+v after %d probes", l.Executor(), probes)
	}
	// committed to the container: the container, and still no second probe
	venue := c.Executor()
	cont := Request{Purpose: PurposeExecute, Tools: true, Executor: &venue}
	if l, err = s.launcher(ctxBg, cont); err != nil || l.Executor().Kind != ExecutorContainer || probes != 0 {
		t.Fatalf("a committed container call: %+v %v after %d probes", l, err, probes)
	}
	// committed to a container this backend does not have: nothing runs
	if _, err := (&Subprocess{Bin: "/usr/bin/claude", Isolation: ExecutorOn}).launcher(ctxBg, cont); !errors.Is(err, ErrBeforeDispatch) {
		t.Fatalf("committed to a container with no container: %v", err)
	}
	// committed to a venue this launcher would not run: the WHOLE venue is
	// the commitment, so a different image, id or network is refused before
	// dispatch rather than dispatched under the record's name (review r2 —
	// the check was on the kind alone, and a retagged image would have run
	// a world the record did not name)
	for _, other := range []Executor{
		{Kind: ExecutorContainer, Image: "img:2", Digest: venue.Digest, Network: venue.Network},
		{Kind: ExecutorContainer, Image: venue.Image, Digest: "sha256:" + strings.Repeat("ad", 32), Network: venue.Network},
		{Kind: ExecutorContainer, Image: venue.Image, Digest: venue.Digest, Network: "none"},
	} {
		o := other
		if _, err := s.launcher(ctxBg, Request{Purpose: PurposeExecute, Tools: true, Executor: &o}); !errors.Is(err, ErrBeforeDispatch) {
			t.Fatalf("committed to %+v: %v", o, err)
		}
	}
	// and when nobody asked (a direct Complete), the backend decides
	if l, err = s.launcher(ctxBg, Request{Purpose: PurposeExecute, Tools: true}); err != nil || l.Executor().Kind != ExecutorContainer || probes == 0 {
		t.Fatalf("an unshelled call: %+v %v after %d probes", l, err, probes)
	}
}

// panicSink panics the moment the stream reports an effect: the way to get a
// panic out of the middle of Complete without a build tag.
type panicSink struct{}

func (panicSink) Observe(EffectEvent) (int, string, error) { panic("sink") }
func (panicSink) Result(EffectResult) error                { return nil }

// The container is ended on EVERY way out of Complete, not only when the
// call's own context was cancelled.
//
// The child process on this lane is the docker CLIENT. It can die while the
// container lives — killed on its own, or dropped by the daemon — and it
// can be unwound by a panic. The first version stopped the container only
// when `cctx.Err() != nil`, which covered the deadline and the operator's
// ^C and missed exactly the cases where the engine did not know the child
// was gone: the engine would record a failure, ingest the drop file and
// shred the hand-off while the worker was still writing (review r2).
func TestTheContainerIsEndedOnEveryWayOut(t *testing.T) {
	// a stand-in docker client: the script ignores the argv it is given,
	// which is what makes it a stand-in for a client that died
	bin := func(t *testing.T, body string) string {
		t.Helper()
		p := filepath.Join(t.TempDir(), "docker")
		if err := os.WriteFile(p, []byte("#!/bin/sh\n"+body+"\n"), 0o700); err != nil {
			t.Fatal(err)
		}
		return p
	}
	setup := func(t *testing.T, body string) (*Subprocess, *Container, *[]string) {
		t.Helper()
		killed := &[]string{}
		c := NewContainer("img:1", "", "", "", "none")
		c.Docker = bin(t, body)
		c.Exec = func(_ context.Context, argv []string) ([]byte, error) {
			line := strings.Join(argv, " ")
			if strings.Contains(line, " kill ") {
				*killed = append(*killed, argv[len(argv)-1])
			}
			return []byte("sha256:" + strings.Repeat("be", 32)), nil
		}
		if err := c.Preflight(ctxBg); err != nil {
			t.Fatal(err)
		}
		return &Subprocess{Bin: "/usr/bin/claude", Model: "m", Container: c, Isolation: ExecutorRequire}, c, killed
	}
	t.Run("a client that dies on its own, with nothing cancelled", func(t *testing.T) {
		s, c, killed := setup(t, "exit 137") // SIGKILLed client
		venue := c.Executor()
		res, err := s.Complete(ctxBg, Request{Purpose: PurposeExecute, Prompt: []byte("go"), Tools: true, Executor: &venue}, nil)
		if err != nil {
			t.Fatal(err)
		}
		if res.Terminal != TerminalFailed {
			t.Fatalf("terminal %v", res.Terminal)
		}
		if len(*killed) != 1 || !strings.HasPrefix((*killed)[0], NamePrefix) {
			t.Fatalf("the container was not ended: %v", *killed)
		}
	})
	t.Run("a panic through the sink", func(t *testing.T) {
		s, c, killed := setup(t, `echo '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"x"}}]}}'`)
		venue := c.Executor()
		func() {
			defer func() {
				if recover() == nil {
					t.Fatal("the sink did not panic; this fixture proves nothing")
				}
			}()
			s.Complete(ctxBg, Request{Purpose: PurposeExecute, Prompt: []byte("go"), Tools: true, Executor: &venue}, panicSink{})
		}()
		if len(*killed) != 1 {
			t.Fatalf("a panic left the container running: %v", *killed)
		}
	})
	t.Run("the worker's channels are ingested only after the work is ended", func(t *testing.T) {
		s, c, killed := setup(t, "exit 0")
		order := []string{}
		c.Exec = func(_ context.Context, argv []string) ([]byte, error) {
			if strings.Contains(strings.Join(argv, " "), " kill ") {
				order = append(order, "stop")
				*killed = append(*killed, "x")
			}
			return []byte("sha256:" + strings.Repeat("be", 32)), nil
		}
		s.AfterTools = func() { order = append(order, "ingest") }
		venue := c.Executor()
		if _, err := s.Complete(ctxBg, Request{Purpose: PurposeExecute, Prompt: []byte("go"), Tools: true, Executor: &venue}, nil); err != nil {
			t.Fatal(err)
		}
		if len(order) != 2 || order[0] != "stop" || order[1] != "ingest" {
			t.Fatalf("order %v: the drop file was read while the worker could still be writing", order)
		}
	})
	t.Run("a container that could not be ended is in the record, not only the notice", func(t *testing.T) {
		s, c, _ := setup(t, `echo '{"type":"result","subtype":"success","is_error":false,"result":"done"}'`)
		notes := []string{}
		s.Notify = func(n string) { notes = append(notes, n) }
		c.Exec = func(_ context.Context, argv []string) ([]byte, error) {
			if strings.Contains(strings.Join(argv, " "), " kill ") {
				return []byte("Error response from daemon: something else"), errors.New("exit 1")
			}
			return []byte("sha256:" + strings.Repeat("be", 32)), nil
		}
		venue := c.Executor()
		res, err := s.Complete(ctxBg, Request{Purpose: PurposeExecute, Prompt: []byte("go"), Tools: true, Executor: &venue}, nil)
		if err != nil {
			t.Fatal(err)
		}
		// the call itself succeeded; the engine cannot say its effects stopped
		if res.Terminal != TerminalPartial || !strings.Contains(res.Reason, "container not ended") {
			t.Fatalf("terminal %v reason %q", res.Terminal, res.Reason)
		}
		if len(notes) != 1 || !strings.Contains(notes[0], "could not end the container") {
			t.Fatalf("notes %v", notes)
		}
	})
	t.Run("a container that could not be ended is not followed by reading what the worker wrote", func(t *testing.T) {
		// The drop file is a channel the worker writes. A container the
		// engine could not end is a worker that can still rewrite it, and
		// ingesting a derived secret from a call the engine has LOST is
		// worse than not ingesting one (review r3).
		s, c, _ := setup(t, `echo '{"type":"result","subtype":"success","is_error":false,"result":"done"}'`)
		ingested := 0
		s.AfterTools = func() { ingested++ }
		s.Notify = func(string) {}
		c.Exec = func(_ context.Context, argv []string) ([]byte, error) {
			if strings.Contains(strings.Join(argv, " "), " kill ") {
				return []byte("Error response from daemon: something else"), errors.New("exit 1")
			}
			return []byte(testID), nil
		}
		venue := c.Executor()
		res, err := s.Complete(ctxBg, Request{Purpose: PurposeExecute, Prompt: []byte("go"), Tools: true, Executor: &venue}, nil)
		if err != nil {
			t.Fatal(err)
		}
		if ingested != 0 {
			t.Fatalf("the worker's channels were read %d time(s) after a container the engine could not end", ingested)
		}
		if !strings.Contains(res.Reason, "channels were not read") {
			t.Fatalf("reason %q", res.Reason)
		}
	})
	t.Run("the normal case: --rm already removed it, which is success", func(t *testing.T) {
		s, c, _ := setup(t, `echo '{"type":"result","subtype":"success","is_error":false,"result":"done"}'`)
		notes := []string{}
		s.Notify = func(n string) { notes = append(notes, n) }
		c.Exec = func(_ context.Context, argv []string) ([]byte, error) {
			if strings.Contains(strings.Join(argv, " "), " kill ") {
				return []byte("Error response from daemon: No such container: maro-exec-go-1-1"), errors.New("exit 1")
			}
			return []byte("sha256:" + strings.Repeat("be", 32)), nil
		}
		venue := c.Executor()
		res, err := s.Complete(ctxBg, Request{Purpose: PurposeExecute, Prompt: []byte("go"), Tools: true, Executor: &venue}, nil)
		if err != nil {
			t.Fatal(err)
		}
		if res.Terminal != TerminalComplete || res.Reason != "" {
			t.Fatalf("terminal %v reason %q: a container that is already gone IS ended", res.Terminal, res.Reason)
		}
		if len(notes) != 0 {
			t.Fatalf("the operator was told about the normal case: %v", notes)
		}
	})
}
