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

// pyWordSupplement is the code points CPython's \w matches and Go's
// \p{L}\p{N} does not, because Go ships unicode 15.0.0 where CPython here
// has 16.0.0 (adversarial r5, L4).
//
// The skew was measured, not estimated: 5,004 code points in 27 runs, and
// ZERO in the other direction. It matters more here than anywhere else in
// the port because the output of this function is a FILENAME — a skill
// whose name contains any of these gets stripped to one slug by Go and
// kept in another by Python, so the two runtimes write the same skill to
// two different files, which this function's own doc calls worse than not
// writing it at all.
//
// 4,617 of the 5,004 are two blocks (Egyptian Hieroglyphs Extended-A and
// CJK Extension I); the rest are newly-encoded scripts. Enumerating 27
// ranges closes it outright, and slug_skew_test.go re-derives the whole set
// from CPython so a gap in either direction fails and a table that has gone
// dead says so.
var pyWordSupplement = [...][2]rune{
	{0x01C89, 0x01C8A}, // Cyrillic Tje
	{0x0A7CB, 0x0A7CD}, // Latin extensions
	{0x0A7DA, 0x0A7DC}, // Latin extensions
	{0x105C0, 0x105F3}, // Todhri
	{0x10D40, 0x10D65}, // Garay
	{0x10D6F, 0x10D85}, // Garay
	{0x10EC2, 0x10EC4}, // Arabic Extended-C
	{0x11380, 0x11389}, // Tulu-Tigalari
	{0x1138B, 0x1138B}, // Tulu-Tigalari
	{0x1138E, 0x1138E}, // Tulu-Tigalari
	{0x11390, 0x113B5}, // Tulu-Tigalari
	{0x113B7, 0x113B7}, // Tulu-Tigalari
	{0x113D1, 0x113D1}, // Tulu-Tigalari
	{0x113D3, 0x113D3}, // Tulu-Tigalari
	{0x116D0, 0x116E3}, // Myanmar Extended-C
	{0x11BC0, 0x11BE0}, // Sunuwar
	{0x11BF0, 0x11BF9}, // Sunuwar
	{0x13460, 0x143FA}, // Egyptian Hieroglyphs Extended-A (3,995)
	{0x16100, 0x1611D}, // Gurung Khema
	{0x16130, 0x16139}, // Gurung Khema
	{0x16D40, 0x16D6C}, // Kirat Rai
	{0x16D70, 0x16D79}, // Kirat Rai
	{0x18CFF, 0x18CFF}, // Khitan Small Script
	{0x1CCF0, 0x1CCF9}, // Outlined digits
	{0x1E5D0, 0x1E5ED}, // Ol Onal
	{0x1E5F0, 0x1E5FA}, // Ol Onal
	{0x2EBF0, 0x2EE5D}, // CJK Extension I (622)
}

// pyWordClass renders pyWordSupplement as regexp character-class ranges.
func pyWordClass() string {
	var b strings.Builder
	for _, rg := range pyWordSupplement {
		if rg[0] == rg[1] {
			fmt.Fprintf(&b, `\x{%04x}`, rg[0])
			continue
		}
		fmt.Fprintf(&b, `\x{%04x}-\x{%04x}`, rg[0], rg[1])
	}
	return b.String()
}

var (
	slugDropRE  = regexp.MustCompile(`[^\p{L}\p{N}_` + pyWordClass() + pySpaceClass + `-]`)
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
