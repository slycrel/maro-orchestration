package record

import (
	"os"
	"strconv"
	"sync"
	"syscall"
	"testing"
)

// The /proc reader must agree with the syscall it replaces. If it ever
// disagreed, every file this runtime created would carry the wrong mode
// and nothing here would fail — the symptom is EACCES in someone ELSE's
// runtime, on a host with a different umask, long after the fact.
func TestTheProcUmaskAgreesWithTheSyscall(t *testing.T) {
	got, ok := umaskFromProc()
	if !ok {
		t.Skip("/proc/self/status carries no Umask line (kernel < 4.7 or no /proc)")
	}
	// Read the authoritative value the racy way, then put it straight back.
	want := syscall.Umask(0)
	syscall.Umask(want)
	if got != want {
		t.Errorf("umaskFromProc() = %#o, syscall says %#o", got, want)
	}
}

// The whole point of preferring /proc is that reading does not disturb the
// value. The old read-back set it to 0 for a moment, and every other
// goroutine creating a file in that window got a world-writable one.
func TestReadingTheUmaskDoesNotChangeIt(t *testing.T) {
	before := syscall.Umask(0)
	syscall.Umask(before)

	if _, ok := umaskFromProc(); !ok {
		t.Skip("/proc/self/status carries no Umask line")
	}
	after := syscall.Umask(0)
	syscall.Umask(after)
	if before != after {
		t.Errorf("umaskFromProc() changed the process umask: %#o -> %#o",
			before, after)
	}
}

// Concurrency is the reason this exists, so it is asserted under
// concurrency: many goroutines reading while many others create files,
// and not one of those files may be wider than the umask allows.
//
// The old swap-and-restore leaked at a measured ~2.5% here. A single
// leaked file is a real one — mode bits do not heal.
func TestNoFileEscapesTheUmaskWhileTheUmaskIsBeingRead(t *testing.T) {
	mask, ok := umaskFromProc()
	if !ok {
		t.Skip("/proc/self/status carries no Umask line")
	}
	if mask == 0 {
		t.Skip("umask 0 cannot distinguish a leak from a correct write")
	}
	dir := t.TempDir()
	const n = 60
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			umaskFromProc()
			f, err := os.OpenFile(dir+"/f"+strconv.Itoa(i),
				os.O_CREATE|os.O_WRONLY, 0o666)
			if err != nil {
				return
			}
			f.Close()
		}(i)
	}
	wg.Wait()

	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	want := os.FileMode(0o666 &^ mask)
	checked := 0
	for _, e := range entries {
		info, err := e.Info()
		if err != nil {
			continue
		}
		checked++
		if info.Mode().Perm() != want {
			t.Errorf("%s created with mode %#o, want %#o — a reader widened "+
				"the umask under it", e.Name(), info.Mode().Perm(), want)
		}
	}
	if checked == 0 {
		t.Fatal("no files were created; this test proved nothing")
	}
}

// A umask line that is not a umask must be REFUSED, not coerced. The
// fallback is racy but correct; a plausible-looking wrong value is not
// recoverable and is silent.
func TestAMalformedUmaskLineIsRefused(t *testing.T) {
	// umaskFromProc reads a fixed path, so the refusals are driven through
	// the PRODUCTION parser directly rather than a copy of it.
	for _, body := range []string{
		"Name:\tgo\n",          // no Umask line at all
		"Umask:\t\n",           // empty
		"Umask:\t00022\n",      // too many digits
		"Umask:\t0o22\n",       // not octal digits
		"Umask:\t-022\n",       // negative
		"Umask:\t7777\n",       // out of range
		"Umask:\tnotanumber\n", // prose
		"Umask:\t0002 extra\n", // trailing junk
		"Umask:\t0089\n",       // decimal-looking, not octal
	} {
		if m, ok := parseUmaskStatus(body); ok {
			t.Errorf("accepted %q as umask %#o", body, m)
		}
	}
	// Acceptance. The values are chosen so each one can only pass if the
	// parser does the specific thing it claims:
	//
	//   0022 reads as 18 in octal and 22 in decimal — the two most common
	//        umasks on this box (0002 and 0022) BOTH read the same either
	//        way, so a base-10 parser passed a whole battery undetected
	//        until this case existed.
	//   the trailing space pins the TrimSpace, which a left-only trim would
	//        leave for ParseInt to choke on.
	for _, c := range []struct {
		body string
		want int
	}{
		{"Name:\tgo\nUmask:\t0002\nPid:\t1\n", 0o002},
		{"Umask:\t0\n", 0},
		{"Umask:\t0022\n", 0o022},
		{"Umask:\t0022 \n", 0o022},
		{"Umask:\t0777\n", 0o777},
	} {
		if m, ok := parseUmaskStatus(c.body); !ok || m != c.want {
			t.Errorf("parseUmaskStatus(%q) = %#o, %v; want %#o, true",
				c.body, m, ok, c.want)
		}
	}
}
