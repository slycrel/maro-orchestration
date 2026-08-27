package pyos

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"syscall"
	"testing"
)

// The point of this test is that the strerror strings are NOT written
// down in this package. It asks CPython for the message and the class of
// every errno the ports can reach and compares them against what
// ErrorText and ErrorClass build from Go's own errno table — so the
// "capitalize Go's text" rule is a measured claim, and a libc whose
// catalogue drifts from Go's fails here rather than in a log line.
var errnos = []syscall.Errno{
	syscall.ENOENT, syscall.EACCES, syscall.EPERM, syscall.EEXIST,
	syscall.ENOTDIR, syscall.EISDIR, syscall.EINVAL, syscall.ENOTEMPTY,
	syscall.ENOSPC, syscall.ELOOP, syscall.ENAMETOOLONG, syscall.EIO,
	syscall.EAGAIN, syscall.ECHILD, syscall.EPIPE, syscall.ESPIPE,
	syscall.EINTR, syscall.EXDEV, syscall.ESHUTDOWN, syscall.ECONNRESET,
	syscall.ECONNREFUSED, syscall.ECONNABORTED, syscall.EINPROGRESS,
	syscall.EALREADY, syscall.EDEADLK, syscall.EMFILE, syscall.EROFS,
	syscall.EBUSY, syscall.EBADF, syscall.EFBIG, syscall.ERANGE,
}

type pyRec struct {
	Errno int    `json:"errno"`
	Text  string `json:"text"`
	Class string `json:"class"`
}

func TestOSErrorTextMatchesCPython(t *testing.T) {
	const path = "/tmp/a dir/o'brien.txt"
	nums := make([]int, len(errnos))
	for i, e := range errnos {
		nums[i] = int(e)
	}
	blob, err := json.Marshal(nums)
	if err != nil {
		t.Fatal(err)
	}
	script := `
import json, os, sys
nums = json.loads(sys.argv[1])
path = sys.argv[2]
out = []
for n in nums:
    exc = OSError(n, os.strerror(n), path)
    out.append({"errno": n, "text": str(exc),
                "class": type(exc).__name__})
print(json.dumps(out))
`
	cmd := exec.Command("python3", "-c", script, string(blob), path)
	raw, err := cmd.Output()
	if err != nil {
		t.Fatalf("python3: %v", err)
	}
	var recs []pyRec
	if err := json.Unmarshal(raw, &recs); err != nil {
		t.Fatal(err)
	}
	if len(recs) != len(errnos) {
		t.Fatalf("got %d records for %d errnos", len(recs), len(errnos))
	}
	for i, e := range errnos {
		got := ErrorText(path, e)
		if got != recs[i].Text {
			t.Errorf("errno %d text:\n go: %s\n py: %s", int(e), got,
				recs[i].Text)
		}
		if cls := ErrorClass(e); cls != recs[i].Class {
			t.Errorf("errno %d class: go %s, py %s", int(e), cls,
				recs[i].Class)
		}
	}
}

// An error with no errno underneath it keeps Go's text verbatim. This is
// the branch that stops the helper from inventing an `[Errno 0]` for a
// failure that never came from a syscall.
func TestErrorTextWithoutErrnoKeepsGoText(t *testing.T) {
	err := fmt.Errorf("not a syscall failure")
	if got := ErrorText("/x", err); got != "not a syscall failure" {
		t.Fatalf("got %q", got)
	}
	if got := ErrorClass(err); got != "OSError" {
		t.Fatalf("class %q", got)
	}
}

func TestCapitalizeLeavesTheRestAlone(t *testing.T) {
	cases := map[string]string{
		"":                   "",
		"is a directory":     "Is a directory",
		"input/output error": "Input/output error",
		"éxit":               "Éxit",
		"Already":            "Already",
	}
	for in, want := range cases {
		if got := capitalize(in); got != want {
			t.Errorf("capitalize(%q) = %q, want %q", in, got, want)
		}
	}
}
