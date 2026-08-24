package notify

import (
	"os"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/testenv"
)

// The hook's own package is not exempt. Its tests inject an Exec recorder
// so no command runs — but `Emit` with no Cfg still falls through to
// `config.LoadFor(ws)`, and several of them pass none, so `notify.events`
// and `notify.timeout_seconds` were being read out of the operator's real
// ~/.maro/config.yml. They passed because that file sets neither; a config
// that set `events` would have turned them red for a reason no comment
// here explained (adversarial r11 round 3, LOW).
func TestMain(m *testing.M) { os.Exit(testenv.Isolate(m)) }
