package selfimprove

import (
	"os"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/testenv"
)

// Reaches notify through the scans lane. See internal/testenv.
func TestMain(m *testing.M) { os.Exit(testenv.Isolate(m)) }
