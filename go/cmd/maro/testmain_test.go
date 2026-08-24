package main

import (
	"os"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/testenv"
)

// The CLI wires every lane, notify's included, so its tests can reach the
// operator's registered hook. See internal/testenv.
func TestMain(m *testing.M) { os.Exit(testenv.Isolate(m)) }
