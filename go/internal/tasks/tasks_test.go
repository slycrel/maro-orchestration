package tasks

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

func ws(t *testing.T) string {
	t.Helper()
	return t.TempDir()
}

func read(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

// pythonLockName asks CPython what it would lock, rather than asserting a
// name this package computed. The finding being pinned is a DISAGREEMENT
// between two runtimes, so one of the two answers has to come from the
// other runtime or the test is just this package agreeing with itself.
func pythonLockName(t *testing.T, path string) string {
	t.Helper()
	out, err := exec.Command("python3", "-c",
		"import pathlib,sys; print(pathlib.Path(sys.argv[1]).with_suffix('.lock'))",
		path).Output()
	if err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	return strings.TrimSpace(string(out))
}

func TestTheLockFileReplacesTheExtensionLikePython(t *testing.T) {
	for _, name := range []string{
		"task-abc.json", "task.v2.json", "task-abc", "task-abc.json.bak",
		// pathlib's suffix is the last dot AT OR AFTER the first non-dot
		// character — not filepath.Ext. Each of these lands somewhere
		// different from what Ext would say, and CPython is the authority
		// on all of them. ("/q/." is left out: pathlib normalises the
		// trailing component away, which is a path question, not a
		// suffix one.)
		".json", ".hidden.json", "..json", "...json", "a..json", "x.", "..",
	} {
		p := filepath.Join("/q", name)
		want := pythonLockName(t, p)
		if got := lockPath(p); got != want {
			t.Errorf("lockPath(%q)\n got %q\nwant %q (CPython's with_suffix)", p, got, want)
		}
	}
}

// The name matching is a proxy; THIS is the consequence. Python takes the
// lock and holds it, and this runtime must not be able to proceed while it
// does. A port that appended ".lock" would sail straight through, and
// every assertion about file contents would still pass.
func TestGoWaitsForALockPythonIsHolding(t *testing.T) {
	dir := ws(t)
	path := TaskPath(dir, "task-shared-0001")
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil {
		t.Fatal(err)
	}

	script := `
import fcntl, pathlib, sys, time
lock = pathlib.Path(sys.argv[1]).with_suffix(".lock")
fp = open(lock, "a")
fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
print("LOCKED", flush=True)
time.sleep(30)
`
	cmd := exec.Command("python3", "-c", script, path)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	defer func() {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	}()

	buf := make([]byte, 6)
	if _, err := stdout.Read(buf); err != nil {
		t.Fatalf("python never reported the lock: %v", err)
	}

	entered := make(chan struct{})
	go func() {
		_ = locked(path, false, func() error { close(entered); return nil })
	}()

	select {
	case <-entered:
		t.Fatal("this runtime entered the critical section while Python held the lock")
	case <-time.After(400 * time.Millisecond):
	}

	_ = cmd.Process.Kill()
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("this runtime never acquired the lock after Python released it")
	}
}

// The whole point of a lock file is that two holders cannot coexist. A
// shared lock is in task_store's signature and unused by its callers; it
// is ported, so it is pinned.
func TestASharedLockAdmitsTwoHoldersAndAnExclusiveOneDoesNot(t *testing.T) {
	dir := ws(t)
	path := TaskPath(dir, "task-sh-0001")
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil {
		t.Fatal(err)
	}
	held := make(chan struct{})
	release := make(chan struct{})
	go func() {
		_ = locked(path, true, func() error {
			close(held)
			<-release
			return nil
		})
	}()
	<-held
	second := make(chan struct{})
	go func() {
		_ = locked(path, true, func() error { close(second); return nil })
	}()
	select {
	case <-second:
	case <-time.After(2 * time.Second):
		close(release)
		t.Fatal("a second SHARED holder was blocked; LOCK_SH is not being requested")
	}
	exclusive := make(chan struct{})
	go func() {
		_ = locked(path, false, func() error { close(exclusive); return nil })
	}()
	select {
	case <-exclusive:
		close(release)
		t.Fatal("an EXCLUSIVE holder got in while a shared lock was held")
	case <-time.After(300 * time.Millisecond):
	}
	close(release)
	select {
	case <-exclusive:
	case <-time.After(5 * time.Second):
		t.Fatal("the exclusive holder never acquired after the shared one released")
	}
}

// The queue file's mode is mkstemp's 0600 and is NOT umask-derived — there
// is no fchmod in task_store._atomic_write, unlike file_lock.atomic_write
// one directory over. Asserting the octal is right HERE (and wrong in the
// record package's test) precisely because the value is not umask-dependent.
func TestTheTaskFileIsNotReadableByTheGroup(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "task-mode-0001"}); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(TaskPath(dir, "task-mode-0001"))
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0o600 {
		t.Errorf("task file mode = %o, want 600", st.Mode().Perm())
	}
	// A rename replaces the inode, so a pre-existing looser mode does not
	// survive an update. Python behaves the same way for the same reason.
	if err := os.Chmod(TaskPath(dir, "task-mode-0001"), 0o666); err != nil {
		t.Fatal(err)
	}
	if _, err := Fail(dir, "task-mode-0001", "x"); err != nil {
		t.Fatal(err)
	}
	st, err = os.Stat(TaskPath(dir, "task-mode-0001"))
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0o600 {
		t.Errorf("mode after rewrite = %o, want 600", st.Mode().Perm())
	}
}

// Measured from CPython on this box: raw UTF-8, not \uXXXX, and a trailing
// newline after the closing brace. Both are per-writer decisions that the
// sidecar writer makes the other way.
func TestTheWriterUsesRawUTF8AndATrailingNewline(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "task-utf-0001", Reason: "café → naïve "}); err != nil {
		t.Fatal(err)
	}
	got := read(t, TaskPath(dir, "task-utf-0001"))
	if !strings.Contains(got, "\"reason\": \"café → naïve \"") {
		t.Errorf("reason was not written as raw UTF-8:\n%s", got)
	}
	if strings.Contains(got, "\\u00e9") {
		t.Error("reason was escaped; this writer passes ensure_ascii=False")
	}
	if !strings.HasSuffix(got, "}\n") {
		t.Errorf("file does not end with `}` and a newline: %q", got[len(got)-8:])
	}
}

// The on-disk key order is the dict-literal order, not alphabetical, and
// result_status/error ride at the TAIL because Python assigns them after
// the dict is built. A reader diffing two runtimes' queue files compares
// bytes; a reordered file is a diff on every line.
func TestTheKeyOrderIsPythonsInsertionOrder(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "task-ord-0001"}); err != nil {
		t.Fatal(err)
	}
	if _, err := Complete(dir, "task-ord-0001", pyval.Obj{{Key: "a", Val: "/x"}}, "incomplete"); err != nil {
		t.Fatal(err)
	}
	want := []string{
		"job_id", "run_id", "lane", "source", "reason", "status", "attempt",
		"parent_job_id", "blocked_by", "continuation_depth", "origin",
		"timestamps", "artifact_paths", "claimed_by_pid", "result_status",
	}
	got := keyOrder(t, read(t, TaskPath(dir, "task-ord-0001")))
	if len(got) != len(want) {
		t.Fatalf("key count = %d, want %d\ngot %v", len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("key %d = %q, want %q\ngot %v", i, got[i], want[i], got)
		}
	}
}

// keyOrder reads the TOP-LEVEL key order back off disk.
func keyOrder(t *testing.T, text string) []string {
	t.Helper()
	v, err := pyval.LoadsOrdered(text)
	if err != nil {
		t.Fatal(err)
	}
	obj, ok := v.(pyval.Obj)
	if !ok {
		t.Fatalf("not an object: %T", v)
	}
	out := []string{}
	for _, f := range obj {
		out = append(out, f.Key)
	}
	return out
}

// A field this port has never heard of must come back where it was, with
// its value intact. Python rewrites the dict it read; so does this.
func TestAForeignFieldSurvivesARewriteAtItsOwnPosition(t *testing.T) {
	dir := ws(t)
	path := TaskPath(dir, "task-foreign-0001")
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil {
		t.Fatal(err)
	}
	raw := `{
  "job_id": "task-foreign-0001",
  "from_the_future": {"nested": [1, 2.50, null]},
  "status": "queued",
  "timestamps": {"queued_at_utc": "2026-01-01T00:00:00Z", "finished_at_utc": ""},
  "claimed_by_pid": null
}
`
	if err := os.WriteFile(path, []byte(raw), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Fail(dir, "task-foreign-0001", "boom"); err != nil {
		t.Fatal(err)
	}
	got := read(t, path)
	if !strings.Contains(got, `"from_the_future"`) {
		t.Fatalf("the unknown field was dropped:\n%s", got)
	}
	// A stored 2.50 must not come back 2.5: LoadsOrdered keeps the source
	// literal, which is the only reason a foreign file is not reformatted.
	if !strings.Contains(got, "2.50") {
		t.Errorf("a foreign number literal was reformatted:\n%s", got)
	}
	order := keyOrder(t, got)
	if order[1] != "from_the_future" {
		t.Errorf("the unknown field moved to position %v, want 1: %v",
			indexOf(order, "from_the_future"), order)
	}
	if order[len(order)-1] != "error" {
		t.Errorf("`error` did not land at the tail: %v", order)
	}
}

func indexOf(s []string, want string) int {
	for i, v := range s {
		if v == want {
			return i
		}
	}
	return -1
}

// _check_cycle tracks the nodes on the CURRENT DFS path and discards on
// the way back out. Without the discard, a DIAMOND — two independent
// paths reaching one shared dependency — is reported as a cycle, and a
// perfectly legal fan-in graph becomes un-enqueueable.
func TestADiamondDependencyIsNotACycle(t *testing.T) {
	dir := ws(t)
	for _, id := range []string{"d"} {
		if _, err := Enqueue(dir, Options{JobID: id}); err != nil {
			t.Fatal(err)
		}
	}
	for _, id := range []string{"b", "c"} {
		if _, err := Enqueue(dir, Options{JobID: id, BlockedBy: []string{"d"}}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := Enqueue(dir, Options{JobID: "a", BlockedBy: []string{"b", "c"}}); err != nil {
		t.Fatalf("a diamond was rejected as a cycle: %v", err)
	}
}

func TestARealCycleIsRefused(t *testing.T) {
	dir := ws(t)
	// x <- y, then ask for y <- x.
	if _, err := Enqueue(dir, Options{JobID: "y", BlockedBy: nil}); err != nil {
		t.Fatal(err)
	}
	if _, err := Enqueue(dir, Options{JobID: "x", BlockedBy: []string{"y"}}); err != nil {
		t.Fatal(err)
	}
	// Rewrite y to depend on x, closing the loop on disk.
	yt, err := readTask(TaskPath(dir, "y"))
	if err != nil {
		t.Fatal(err)
	}
	yt.Set("blocked_by", pyval.List{"x"})
	if err := writeTask(TaskPath(dir, "y"), yt); err != nil {
		t.Fatal(err)
	}
	_, err = Enqueue(dir, Options{JobID: "z", BlockedBy: []string{"x"}})
	var ce *CycleError
	if !errors.As(err, &ce) {
		t.Fatalf("expected a CycleError, got %v", err)
	}
	// A rejected enqueue leaves nothing behind: the check runs before the
	// write, so a cycle never creates a half-task someone has to reap.
	if _, err := os.Stat(TaskPath(dir, "z")); !os.IsNotExist(err) {
		t.Error("a rejected enqueue wrote its task file anyway")
	}
}

func TestATaskThatDependsOnItselfIsRefused(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "self", BlockedBy: []string{"self"}}); err == nil {
		t.Fatal("a self-dependency was accepted")
	}
}

// Python's list.remove drops ONE occurrence, so a duplicate blocked_by
// entry SURVIVES a completion. Carried verbatim, because a Go runtime that
// removed all copies would write a different blocked_by than Python for
// the same sequence of calls, and these files are read by both.
//
// What it does NOT do, contrary to the note this port was written from, is
// keep the dependent blocked: claim() gates on each dependency's STATUS,
// not on blocked_by being empty, and the leftover id still points at a
// done task. The test below asserted un-claimability, failed, and that is
// how the wrong inference was caught — the residue is cosmetic.
func TestADuplicateDependencySurvivesOneCompletion(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "dep"}); err != nil {
		t.Fatal(err)
	}
	if _, err := Enqueue(dir, Options{JobID: "dup", BlockedBy: []string{"dep", "dep"}}); err != nil {
		t.Fatal(err)
	}
	if _, err := Complete(dir, "dep", nil, ""); err != nil {
		t.Fatal(err)
	}
	after, err := readTask(TaskPath(dir, "dup"))
	if err != nil {
		t.Fatal(err)
	}
	got, err := blockedIter(after)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0] != "dep" {
		t.Errorf("blocked_by = %v, want exactly one leftover copy of dep", got)
	}
	// The stale entry does not block: its target is done.
	if _, err := Claim(dir, "dup", os.Getpid()); err != nil {
		t.Errorf("the leftover duplicate blocked a claim it should not: %v", err)
	}
}

func TestASingleDependencyUnblocksOnCompletion(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "dep"}); err != nil {
		t.Fatal(err)
	}
	if _, err := Enqueue(dir, Options{JobID: "waiter", BlockedBy: []string{"dep"}}); err != nil {
		t.Fatal(err)
	}
	if _, err := Claim(dir, "waiter", os.Getpid()); err == nil {
		t.Fatal("a blocked task was claimable")
	}
	if _, err := Complete(dir, "dep", nil, ""); err != nil {
		t.Fatal(err)
	}
	if _, err := Claim(dir, "waiter", os.Getpid()); err != nil {
		t.Fatalf("the task did not unblock: %v", err)
	}
}

// complete() accepts "queued" as well as "claimed" — a task can be
// completed without ever having been claimed.
func TestAQueuedTaskCanBeCompletedWithoutBeingClaimed(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "task-nq-0001"}); err != nil {
		t.Fatal(err)
	}
	got, err := Complete(dir, "task-nq-0001", nil, "")
	if err != nil {
		t.Fatalf("completing a queued task was refused: %v", err)
	}
	if got.GetString("status") != "done" {
		t.Errorf("status = %q, want done", got.GetString("status"))
	}
	if _, err := Complete(dir, "task-nq-0001", nil, ""); err == nil {
		t.Error("completing an already-done task was accepted")
	}
}

func TestClaimingATaskHeldByALivePIDIsRefused(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "task-live-0001"}); err != nil {
		t.Fatal(err)
	}
	if _, err := Claim(dir, "task-live-0001", os.Getpid()); err != nil {
		t.Fatal(err)
	}
	_, err := Claim(dir, "task-live-0001", os.Getpid())
	var ce *ConflictError
	if !errors.As(err, &ce) {
		t.Fatalf("expected a ConflictError, got %v", err)
	}
	after, err := readTask(TaskPath(dir, "task-live-0001"))
	if err != nil {
		t.Fatal(err)
	}
	if pyval.IntOf(mustGet(after, "attempt")) != 1 {
		t.Errorf("a refused claim still bumped attempt to %v",
			mustGet(after, "attempt"))
	}
}

// A claim held by a dead process is released rather than parked forever.
// The pid used is a real one that has exited, not a made-up number: a
// number that was never allocated and one that has been reaped are the
// same to os.kill, but only the second is the case this recovers from.
func TestAClaimHeldByADeadPIDIsRecovered(t *testing.T) {
	dir := ws(t)
	dead := deadPID(t)
	if _, err := Enqueue(dir, Options{JobID: "task-dead-0001"}); err != nil {
		t.Fatal(err)
	}
	if _, err := Claim(dir, "task-dead-0001", dead); err != nil {
		t.Fatal(err)
	}
	got, err := Claim(dir, "task-dead-0001", os.Getpid())
	if err != nil {
		t.Fatalf("a dead claimer's task was not recoverable: %v", err)
	}
	if pyval.IntOf(mustGet(got, "claimed_by_pid")) != os.Getpid() {
		t.Errorf("claimed_by_pid = %v, want this process", mustGet(got, "claimed_by_pid"))
	}
	if pyval.IntOf(mustGet(got, "attempt")) != 2 {
		t.Errorf("attempt = %v, want 2 (both claims count)", mustGet(got, "attempt"))
	}
}

func TestRecoverStaleClaimsResetsOnlyTheDeadOnes(t *testing.T) {
	dir := ws(t)
	dead := deadPID(t)
	for _, id := range []string{"stale", "live", "idle"} {
		if _, err := Enqueue(dir, Options{JobID: id}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := Claim(dir, "stale", dead); err != nil {
		t.Fatal(err)
	}
	if _, err := Claim(dir, "live", os.Getpid()); err != nil {
		t.Fatal(err)
	}
	got, err := RecoverStaleClaims(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0] != "stale" {
		t.Fatalf("recovered = %v, want [stale]", got)
	}
	live, err := readTask(TaskPath(dir, "live"))
	if err != nil {
		t.Fatal(err)
	}
	if live.GetString("status") != "claimed" {
		t.Error("a live claim was recovered")
	}
}

// deadPID forks a real child, waits for it, and returns its pid.
func deadPID(t *testing.T) int {
	t.Helper()
	cmd := exec.Command("/bin/true")
	if err := cmd.Run(); err != nil {
		t.Fatal(err)
	}
	pid := cmd.Process.Pid
	if pidAlive(pid) {
		t.Skipf("pid %d was recycled before the check", pid)
	}
	return pid
}

func TestPIDAliveAgreesWithTheOSAboutThisProcess(t *testing.T) {
	if !pidAlive(os.Getpid()) {
		t.Error("this process reported itself dead")
	}
	if pidAlive(0) || pidAlive(-1) {
		t.Error("a non-positive pid reported alive")
	}
	// pid 1 exists and is not ours, so os.kill raises EPERM rather than
	// ESRCH. Reading EPERM as "gone" is the 2026-07-08 macOS bug in a
	// different disguise: a task claimed by another user's process would
	// look stale and be stolen.
	if err := syscall.Kill(1, 0); errors.Is(err, syscall.EPERM) && !pidAlive(1) {
		t.Error("EPERM was read as `not alive`")
	}
}

// A torn task file FAILS the sweep rather than being skipped. This is the
// opposite of the announced-read posture everywhere else in this port, and
// it is Python's behaviour: json.loads raises. A queue that quietly
// under-reports its own contents is how a claimed task disappears.
func TestATornTaskFileFailsTheSweepRatherThanVanishing(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "task-ok-0001"}); err != nil {
		t.Fatal(err)
	}
	torn := filepath.Join(TasksDir(dir), "task-torn-0002.json")
	if err := os.WriteFile(torn, []byte(`{"job_id": "task-torn-0002", "sta`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := List(dir, ""); err == nil {
		t.Error("List skipped a torn file instead of failing")
	}
	if _, err := StatusSummary(dir); err == nil {
		t.Error("StatusSummary skipped a torn file instead of failing")
	}
}

func TestListIsSortedAndFilterable(t *testing.T) {
	dir := ws(t)
	for _, id := range []string{"c", "a", "b"} {
		if _, err := Enqueue(dir, Options{JobID: id}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := Fail(dir, "b", "x"); err != nil {
		t.Fatal(err)
	}
	all, err := List(dir, "")
	if err != nil {
		t.Fatal(err)
	}
	var ids []string
	for _, tk := range all {
		// List hands back what _read_task decoded, which for a
		// well-formed row is a mapping.
		m, err := asMapping(tk)
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, m.GetString("job_id"))
	}
	if strings.Join(ids, ",") != "a,b,c" {
		t.Errorf("List order = %v, want a,b,c", ids)
	}
	queued, err := List(dir, "queued")
	if err != nil {
		t.Fatal(err)
	}
	if len(queued) != 2 {
		t.Errorf("queued count = %d, want 2", len(queued))
	}
	counts, err := StatusSummary(dir)
	if err != nil {
		t.Fatal(err)
	}
	if counts["queued"] != 2 || counts["failed"] != 1 {
		t.Errorf("summary = %v, want queued:2 failed:1", counts)
	}
}

// archive() unlinks the lock file WHILE HOLDING IT. That is safe because
// flock lives on the open file description, not the name — but only if the
// next caller opens the lock afresh, which is why this asserts a second
// operation still works afterwards rather than just checking the file is
// gone.
func TestArchiveRemovesTheTaskAndItsLockAndStaysUsable(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "task-arch-0001"}); err != nil {
		t.Fatal(err)
	}
	if _, err := Complete(dir, "task-arch-0001", nil, ""); err != nil {
		t.Fatal(err)
	}
	got, err := Archive(dir, "task-arch-0001")
	if err != nil {
		t.Fatal(err)
	}
	if got.GetString("status") != "archived" {
		t.Errorf("status = %q, want archived", got.GetString("status"))
	}
	if _, err := os.Stat(TaskPath(dir, "task-arch-0001")); !os.IsNotExist(err) {
		t.Error("the task file survived archiving")
	}
	if _, err := os.Stat(lockPath(TaskPath(dir, "task-arch-0001"))); !os.IsNotExist(err) {
		t.Error("the lock file survived archiving")
	}
	archived := filepath.Join(ArchiveDir(dir), "task-arch-0001.json")
	if !strings.Contains(read(t, archived), `"status": "archived"`) {
		t.Error("the archive copy does not carry the archived status")
	}
	// The lock is reusable: a fresh task of the same id can be enqueued
	// and locked again.
	if _, err := Enqueue(dir, Options{JobID: "task-arch-0001"}); err != nil {
		t.Fatalf("the id was unusable after archiving: %v", err)
	}
}

func TestOnlyDoneOrFailedTasksArchive(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "task-q-0001"}); err != nil {
		t.Fatal(err)
	}
	_, err := Archive(dir, "task-q-0001")
	var ce *ConflictError
	if !errors.As(err, &ce) {
		t.Fatalf("expected a ConflictError archiving a queued task, got %v", err)
	}
}

func TestOperationsOnAMissingTaskReportNotFound(t *testing.T) {
	dir := ws(t)
	for name, fn := range map[string]func() error{
		"claim":    func() error { _, e := Claim(dir, "nope", 0); return e },
		"complete": func() error { _, e := Complete(dir, "nope", nil, ""); return e },
		"fail":     func() error { _, e := Fail(dir, "nope", "x"); return e },
		"archive":  func() error { _, e := Archive(dir, "nope"); return e },
	} {
		if err := fn(); !errors.Is(err, ErrNotFound) {
			t.Errorf("%s on a missing task: got %v, want ErrNotFound", name, err)
		}
	}
}

// artifact_paths is a dict.update: an existing key is replaced in place
// and a new one is appended, so two completions merge rather than clobber.
func TestArtifactPathsMergeInPlace(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "task-art-0001"}); err != nil {
		t.Fatal(err)
	}
	path := TaskPath(dir, "task-art-0001")
	tk, err := readTask(path)
	if err != nil {
		t.Fatal(err)
	}
	tk.Set("artifact_paths", pyval.Obj{{Key: "log", Val: "/old"}, {Key: "keep", Val: "/k"}})
	if err := writeTask(path, tk); err != nil {
		t.Fatal(err)
	}
	if _, err := Complete(dir, "task-art-0001",
		pyval.Obj{{Key: "log", Val: "/new"}, {Key: "extra", Val: "/e"}}, ""); err != nil {
		t.Fatal(err)
	}
	got, err := readTask(path)
	if err != nil {
		t.Fatal(err)
	}
	// The BYTES, not the parsed object: LoadsOrdered collapses duplicate
	// keys the way Python's json.loads does, so a writer that emitted
	// "log" twice reads back as one key and looks correct. A read-back
	// through a normalising loader cannot see a writer that duplicates —
	// which is the r4 H1 lesson arriving from the other side.
	if n := strings.Count(read(t, path), `"log":`); n != 1 {
		t.Errorf(`the file carries "log" %d times, want 1`, n)
	}
	paths, _ := mustGet(got, "artifact_paths").(pyval.Obj)
	if len(paths) != 3 {
		t.Fatalf("artifact_paths = %v, want 3 entries", paths)
	}
	if paths[0].Key != "log" || paths[0].Val != "/new" {
		t.Errorf("an updated key moved or did not update: %v", paths)
	}
	if paths[2].Key != "extra" {
		t.Errorf("a new key did not land at the tail: %v", paths)
	}
}

// NewJobID and run_id must not repeat. A clock-derived id collides for two
// tasks enqueued inside the same second, which is exactly what a fan-out
// does.
func TestIdentifiersAreUniqueWithinASecond(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 500; i++ {
		id := NewJobID()
		if seen[id] {
			t.Fatalf("duplicate job id after %d draws: %s", i, id)
		}
		seen[id] = true
	}
	runs := map[string]bool{}
	for i := 0; i < 500; i++ {
		id := newRunID()
		if len(id) != 36 || id[14] != '4' {
			t.Fatalf("run id is not a uuid4: %q", id)
		}
		if v := id[19]; v != '8' && v != '9' && v != 'a' && v != 'b' {
			t.Fatalf("run id has the wrong variant nibble: %q", id)
		}
		if runs[id] {
			t.Fatalf("duplicate run id after %d draws", i)
		}
		runs[id] = true
	}
}

func TestConcurrentClaimsElectExactlyOneWinner(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "task-race-0001"}); err != nil {
		t.Fatal(err)
	}
	const n = 12
	results := make(chan error, n)
	start := make(chan struct{})
	for i := 0; i < n; i++ {
		go func() {
			<-start
			_, err := Claim(dir, "task-race-0001", os.Getpid())
			results <- err
		}()
	}
	close(start)
	won := 0
	for i := 0; i < n; i++ {
		if err := <-results; err == nil {
			won++
		}
	}
	if won != 1 {
		t.Errorf("%d claimants succeeded, want exactly 1", won)
	}
	after, err := readTask(TaskPath(dir, "task-race-0001"))
	if err != nil {
		t.Fatal(err)
	}
	if got := pyval.IntOf(mustGet(after, "attempt")); got != 1 {
		t.Errorf("attempt = %d after a 12-way race, want 1", got)
	}
}

// The temp file must be created in the DESTINATION directory. A temp file
// somewhere else renames fine on this box, where /tmp and the workspace
// share a filesystem, and fails with EXDEV on any host where they do not —
// a bug that cannot reproduce where it was written. Pointing TMPDIR at a
// path that does not exist makes the difference observable without needing
// a second filesystem.
func TestTheTempFileIsWrittenBesideItsDestination(t *testing.T) {
	dir := ws(t)
	t.Setenv("TMPDIR", filepath.Join(dir, "does-not-exist"))
	if _, err := Enqueue(dir, Options{JobID: "task-tmp-0001"}); err != nil {
		t.Fatalf("the writer used the system temp dir instead of the target dir: %v", err)
	}
	if _, err := os.Stat(TaskPath(dir, "task-tmp-0001")); err != nil {
		t.Fatal(err)
	}
	// And it leaves nothing behind.
	entries, err := os.ReadDir(TasksDir(dir))
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".tmp") {
			t.Errorf("a temp file was stranded: %s", e.Name())
		}
	}
}

// An unreadable task file is an ERROR, not a silently missing row. The
// torn-file pin covers a malformed one; this covers a present file the
// process cannot open, which is the case that would otherwise make a whole
// queue look empty because of one permission bit.
func TestAnUnreadableTaskFileFailsTheSweep(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root reads everything; the permission bit is not observable")
	}
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "task-perm-0001"}); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(TaskPath(dir, "task-perm-0001"), 0o000); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(TaskPath(dir, "task-perm-0001"), 0o600) })
	if _, err := List(dir, ""); err == nil {
		t.Error("List reported success over an unreadable task file")
	}
	if _, err := StatusSummary(dir); err == nil {
		t.Error("StatusSummary reported success over an unreadable task file")
	}
}

// Go has no argument defaults, so Python's `lane="now", source="task_store"`
// had to be re-expressed. A dropped default writes an empty lane into every
// row a Go caller enqueues, and nothing downstream errors — the rows simply
// stop matching any lane filter.
func TestTheLibraryDefaultsMatchPythonsKeywordDefaults(t *testing.T) {
	dir := ws(t)
	got, err := Enqueue(dir, Options{JobID: "task-def-0001"})
	if err != nil {
		t.Fatal(err)
	}
	if got.GetString("lane") != "now" {
		t.Errorf("lane = %q, want now", got.GetString("lane"))
	}
	if got.GetString("source") != "task_store" {
		t.Errorf("source = %q, want task_store", got.GetString("source"))
	}
	// An explicit value still wins over the default.
	got, err = Enqueue(dir, Options{JobID: "task-def-0002", Lane: "agenda", Source: "cli"})
	if err != nil {
		t.Fatal(err)
	}
	if got.GetString("lane") != "agenda" || got.GetString("source") != "cli" {
		t.Errorf("explicit values were overridden: %v %v",
			got.GetString("lane"), got.GetString("source"))
	}
}

// MakeTask must COPY the caller's origin. Sharing the backing array means a
// caller that reuses one Options value across a fan-out gets tasks whose
// ancestry mutates behind them — and ancestry is the field whose whole job
// is saying which work spawned this one.
func TestTheOriginIsCopiedNotAliased(t *testing.T) {
	caller := pyval.Obj{{Key: "parent_loop_id", Val: "L1"}}
	task := MakeTask("task-alias-0001", Options{Origin: caller})
	caller.Set("parent_loop_id", "MUTATED")
	caller.Set("added_later", "x")
	origin, _ := mustGet(task, "origin").(pyval.Obj)
	if len(origin) != 1 || origin[0].Val != "L1" {
		t.Errorf("the task's origin followed the caller's slice: %v", origin)
	}
}

// A row with no status counts as "unknown" rather than being dropped. The
// difference only shows on a foreign or half-written row, which is exactly
// when an operator is reading the summary to find out what is going on.
func TestAStatuslessRowCountsAsUnknown(t *testing.T) {
	dir := ws(t)
	if _, err := Enqueue(dir, Options{JobID: "task-known-0001"}); err != nil {
		t.Fatal(err)
	}
	odd := filepath.Join(TasksDir(dir), "task-odd-0002.json")
	if err := os.WriteFile(odd, []byte(`{"job_id": "task-odd-0002"}`+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	counts, err := StatusSummary(dir)
	if err != nil {
		t.Fatal(err)
	}
	if counts["unknown"] != 1 {
		t.Errorf("summary = %v, want one `unknown`", counts)
	}
	total := 0
	for _, v := range counts {
		total += v
	}
	if total != 2 {
		t.Errorf("summary totals %d rows, want 2 — a row was dropped", total)
	}
}
