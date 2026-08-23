package skills

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Python's `re.sub(r"[^\w\s-]", "", s)` on a str. Python's \w is UNICODE:
// letters, digits (Nd/Nl/No) and underscore, so "café" and "日本語" survive
// where Go's ASCII-only \w would strip them to "caf" and "". Emoji are So,
// which is in neither. Measured both sides rather than read from docs:
//
//	'café-résumé!'   -> 'café-résumé'
//	'日本語 skill'    -> '日本語_skill'
//	'emoji 🎉 here'  -> 'emoji_here'
//	'a.b/c'          -> 'abc'
//
// \s is Python's whitespace set, which includes U+00A0 and U+001C-001F —
// neither of which is in Go's \s or in \p{Z}.
const pySpaceClass = `\t\n\v\f\r \x1c-\x1f\x{0085}\x{00a0}\x{1680}` +
	`\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}`

var (
	slugDropRE  = regexp.MustCompile(`[^\p{L}\p{N}_` + pySpaceClass + `-]`)
	slugSpaceRE = regexp.MustCompile(`[` + pySpaceClass + `_]+`)
)

// Slugify is skill_loader._slugify: the skill NAME becomes a filename.
// Two runtimes disagreeing here would write the same skill to two
// different files, which is worse than not writing it at all.
func Slugify(name string) string {
	s := pytext.Lower(pytext.Strip(name))
	s = slugDropRE.ReplaceAllString(s, "")
	s = slugSpaceRE.ReplaceAllString(s, "_")
	s = strings.Trim(s, "_-")
	if s == "" {
		return "unnamed_skill"
	}
	return s
}

// pyPercent0 is Python's format(f, ".0%").
//
// Both runtimes scale by 100 and render with round-half-to-EVEN, which is
// why 0.995 renders "100%" and 0.125 renders "12%" rather than "13%".
//
// This function was first written as a decimal-string shift plus a
// hand-rolled half-even rounder, on the belief that `0.855 * 100` is
// 85.49999999999999 and would render "85" where Python renders "86". That
// belief was asserted from memory and never measured, and it is FALSE:
// 0.855 * 100 is exactly 85.5 in IEEE-754, in both runtimes. The r4
// mutation battery caught it — a mutant replacing the whole thing with the
// two lines below SURVIVED, because the two are equivalent.
//
// Measured basis, not reasoning: 201,810 values across [0,1] — every
// k/1000, every k/800, every k/7, and 200,000 random doubles — rendered
// identically by this expression and by CPython's `format(f, '.0%')`,
// 0 mismatches. The domain matters and is real: success_rate is a rate,
// so it is in [0,1], and that is where the equivalence was checked.
func pyPercent0(f float64) string {
	return strconv.FormatFloat(f*100, 'f', 0, 64) + "%"
}

// ExportSkillAsMarkdown writes a runtime Skill as a SKILL.md curated file
// in the WORKSPACE skills overlay, and reports the path it wrote (empty
// when it skipped).
//
// Python calls this on every promotion to `established`. This port did the
// tier write and the SKILL_PROMOTED event and stopped (adversarial r4,
// M3), so a skill Go promoted was established in skills.jsonl with no
// curated markdown — and Python would never create it later, because the
// promotion sweep only considers skills still at `provisional`. The two
// runtimes' overlays diverged permanently, one skill at a time, with every
// subsequent Python prompt assembly injecting one fewer curated skill and
// nothing anywhere saying so.
//
// The target is the workspace overlay, NOT the repo skill set: this is a
// runtime self-writer, and Python's own comment records that defaulting to
// the repo leaked untracked .md files into the checkout on every promotion.
//
// An existing file is left alone unless overwrite is set — a human may
// have curated it since.
func ExportSkillAsMarkdown(ws string, s Skill, overwrite bool) (string, error) {
	dir := filepath.Join(ws, "skills")
	slug := Slugify(s.Name)
	dest := filepath.Join(dir, slug+".md")
	if _, err := os.Stat(dest); err == nil && !overwrite {
		return "", nil
	}

	triggers := s.TriggerPatterns
	if len(triggers) > 8 {
		triggers = triggers[:8]
	}
	quoted := make([]string, 0, len(triggers))
	for _, t := range triggers {
		quoted = append(quoted, pytext.Repr(t))
	}

	// The frontmatter's `name` is the SLUG, not the display name — the
	// display name is the H1 below. Carried verbatim; they differ whenever
	// a name has spaces or capitals, which is most of them.
	lines := []string{
		"---",
		"name: " + slug,
		`description: "` + s.Description + `"`,
		"roles_allowed: [worker]",
		"triggers: [" + strings.Join(quoted, ", ") + "]",
	}
	if s.OptimizationObjective != "" {
		lines = append(lines, `optimization_objective: "`+s.OptimizationObjective+`"`)
	}
	lines = append(lines,
		"---",
		"",
		"# "+s.Name,
		"",
		fmt.Sprintf("> Auto-extracted from runtime skill (tier: %s, use_count: %d, success_rate: %s)",
			s.Tier, s.UseCount, pyPercent0(s.SuccessRate)),
		"",
	)
	if s.Description != "" {
		lines = append(lines, s.Description, "")
	}
	if len(s.StepsTemplate) > 0 {
		lines = append(lines, "## Steps", "")
		for i, step := range s.StepsTemplate {
			lines = append(lines, fmt.Sprintf("%d. %s", i+1, step))
		}
		lines = append(lines, "")
	}
	lines = append(lines,
		"## Stats",
		fmt.Sprintf("- Uses: %d", s.UseCount),
		"- Success rate: "+pyPercent0(s.SuccessRate),
		"- Tier: "+s.Tier,
		"",
	)

	if err := os.MkdirAll(dir, record.NewDirMode); err != nil {
		return "", err
	}
	if err := record.AtomicWrite(dest, []byte(strings.Join(lines, "\n"))); err != nil {
		return "", err
	}
	return dest, nil
}
