//go:build race

package guard

import "time"

// Under -race every memory access is instrumented, which costs roughly
// an order of magnitude. Measured on this box: the same blob scans in
// ~0.7s without the detector and lands within a few percent of the
// plain 10s ceiling with it, so `go test -race ./internal/guard/` was
// failing on instrumentation overhead rather than on any regression —
// a flake that reads exactly like the alarm it is supposed to raise.
//
// The alarm still works: quadratic behaviour on 150k candidates does not
// finish in two minutes either. Raising the ceiling under the build tag
// keeps the signal and drops the false one (found while racing the
// touched packages for adversarial mission-r6).
const urlScanCeiling = 120 * time.Second
