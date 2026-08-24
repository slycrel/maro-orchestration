package scans

import (
	"os"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/testenv"
)

// r8_diff_test.go calls notifyVerdict directly, which emits
// self_improvement_verdict — also default-ON. See internal/testenv.
func TestMain(m *testing.M) { os.Exit(testenv.Isolate(m)) }
