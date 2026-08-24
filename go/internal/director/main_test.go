package director

import (
	"os"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/testenv"
)

// This package's tests fire recursion check-ins, and a check-in is a
// default-ON notify event. Without this, notify.Emit falls back to
// config.Load() and runs the OPERATOR'S registered notify.command — which on
// the box this port is written on messages Telegram and ssh's to another
// host. See internal/testenv.
func TestMain(m *testing.M) { os.Exit(testenv.Isolate(m)) }
