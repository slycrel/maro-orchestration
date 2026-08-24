package handlequeue

import (
	"os"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/testenv"
)

// The drain reaches notify twice — the surfaced-escalation emit and the
// task_drained row — and Options.Notify is left zero in every fixture
// here, so `Emit` falls through to `config.LoadFor(ws)` and reads the
// OPERATOR's ~/.maro/config.yml. On this box that file registers a
// notify.command which messages Telegram and ssh's to another host.
//
// The package shipped without this and the round-6 full-suite run caught
// it — not a reviewer, the tripwire in internal/testenv, which is the
// thing that check exists for.
func TestMain(m *testing.M) { os.Exit(testenv.Isolate(m)) }
