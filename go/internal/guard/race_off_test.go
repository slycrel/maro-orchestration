//go:build !race

package guard

import "time"

// urlScanCeiling is the wall-clock budget TestURLScanStaysLinear allows
// for a ~1.2MB blob. It is a QUADRATIC-REGRESSION alarm, not a
// performance budget: the linear scan runs in well under a second here,
// and a quadratic one does not finish at all.
const urlScanCeiling = 10 * time.Second
