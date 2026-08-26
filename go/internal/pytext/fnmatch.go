package pytext

import "strings"

// FnMatch is Python's `fnmatch.fnmatchcase` — the matcher behind
// `pathlib.Path.glob` for a single path component.
//
// # Why this is not filepath.Match
//
// Every other glob in this port uses a CONSTANT pattern ("*.json",
// "????-??-??.md") where Go and Python agree, so filepath.Match was always
// safe. This one is built from a SKILL NAME, and the two languages disagree
// on exactly the characters an attacker-or-accident supplies:
//
//   - BACKSLASH. Go's filepath.Match treats `\` as an escape for the next
//     character. Python's fnmatch does not — `\` is a literal. A skill
//     named `a\*b` matches the literal name in Go and matches `a\` followed
//     by anything in Python.
//   - AN UNCLOSED BRACKET. Go returns ErrBadPattern, and every caller that
//     ignores the error silently gets NO matches. Python treats a `[` with
//     no closing `]` as a literal `[`. Measured: a skill named `a[b` globs
//     to `[]` in CPython because no file is literally named `a[b_*.json` —
//     not because the pattern was rejected. Those coincide here and would
//     not for a skill whose name really does contain a bracket.
//
// # The metacharacters are NOT escaped, and that is Python's behaviour
//
// `prov_dir.glob(f"{skill_name}_*.json")` interpolates the name straight
// into the pattern. Measured on this box, with four sidecars present:
//
//	'a*b'  -> ['ab_1.json', 'axb_1.json']   <- OTHER skills' provenance
//	'a?b'  -> ['axb_1.json']
//	'a[b]' -> ['ab_1.json']                 <- and NOT its own a[b]_1.json
//
// So a skill whose name carries a metacharacter reads records belonging to
// other skills and misses its own. That is a real Python defect, and this
// port REPRODUCES it rather than fixing it, because the two runtimes read
// one shared store and an audit that answered differently depending on
// which runtime asked would be worse than one that is wrong the same way
// twice. LoadSkillProvenance names it at the call site.
//
// # Rules, from CPython's fnmatch.translate
//
//   - `*` matches any sequence, including empty.
//   - `?` matches exactly one character.
//   - `[seq]` is a character class; `[!seq]` negates it. A `]` immediately
//     after the opening (or after the `!`) is a LITERAL `]`, not the close.
//     A `-` first or last in the class is literal.
//   - anything else, `\` included, is literal.
//
// Matching is over RUNES, not bytes, because `?` counts characters.
func FnMatch(name, pattern string) bool {
	return fnMatchRunes([]rune(name), []rune(pattern))
}

func fnMatchRunes(name, pat []rune) bool {
	// Iterative backtracking on `*` rather than recursion: a pattern of many
	// stars against a long name is quadratic here and exponential the naive
	// recursive way, and both inputs are attacker-shaped.
	var (
		ni, pi   int
		starPat  = -1
		starName int
		haveStar bool
	)
	for ni < len(name) {
		if pi < len(pat) {
			switch pat[pi] {
			case '*':
				starPat, starName, haveStar = pi, ni, true
				pi++
				continue
			case '?':
				ni++
				pi++
				continue
			case '[':
				if end, ok := classEnd(pat, pi); ok {
					if classMatches(pat[pi+1:end], name[ni]) {
						ni++
						pi = end + 1
						continue
					}
					// A class that does not match is a plain mismatch;
					// fall through to the backtrack below.
					break
				}
				// An unclosed '[' is a LITERAL '[' — Python's rule.
				if name[ni] == '[' {
					ni++
					pi++
					continue
				}
			default:
				if name[ni] == pat[pi] {
					ni++
					pi++
					continue
				}
			}
		}
		if haveStar {
			// The last star swallows one more character and we retry.
			starName++
			ni = starName
			pi = starPat + 1
			continue
		}
		return false
	}
	// Trailing stars may match the empty remainder; nothing else may.
	for pi < len(pat) && pat[pi] == '*' {
		pi++
	}
	return pi == len(pat)
}

// classEnd finds the index of the `]` closing the class that opens at
// pat[i], honouring Python's rule that a `]` in the FIRST position (after an
// optional `!`) is a literal member rather than the terminator. It reports
// false when there is no closing bracket at all, which makes the `[`
// literal.
func classEnd(pat []rune, i int) (int, bool) {
	j := i + 1
	if j < len(pat) && pat[j] == '!' {
		j++
	}
	if j < len(pat) && pat[j] == ']' {
		j++ // a leading ']' is a member, not the close
	}
	for ; j < len(pat); j++ {
		if pat[j] == ']' {
			return j, true
		}
	}
	return 0, false
}

// classMatches tests one character against the body of a `[...]` class —
// the runes BETWEEN the brackets, negation marker included.
func classMatches(body []rune, c rune) bool {
	negate := false
	if len(body) > 0 && body[0] == '!' {
		negate = true
		body = body[1:]
	}
	hit := false
	for i := 0; i < len(body); i++ {
		// A '-' is a range only BETWEEN two members; first or last it is a
		// literal hyphen.
		if i+2 < len(body) && body[i+1] == '-' {
			if c >= body[i] && c <= body[i+2] {
				hit = true
			}
			i += 2
			continue
		}
		if body[i] == c {
			hit = true
		}
	}
	return hit != negate
}

// HasGlobMeta reports whether a string carries a character fnmatch would
// treat as a metacharacter. It is not used to escape anything — see
// FnMatch's note on why the port reproduces Python's non-escaping — but a
// caller that wants to WARN about a name whose lookup will misbehave needs
// to ask the question, and asking it in one place keeps the answer aligned
// with the matcher.
func HasGlobMeta(s string) bool {
	return strings.ContainsAny(s, "*?[")
}
