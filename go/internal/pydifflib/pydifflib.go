// Package pydifflib is a behavioural port of the part of CPython's
// difflib.SequenceMatcher that answers `SequenceMatcher(None, a, b).ratio()`
// for two `str`.
//
// Ported from the interpreter on this box — CPython 3.14.3,
// Lib/difflib.py: `_calculate_ratio` (line 39), `SequenceMatcher.__chain_b`
// (266), `find_longest_match` (305), `get_matching_blocks` (421) and
// `ratio` (597). Statement order, the dictionary dance in
// find_longest_match, the LIFO queue in get_matching_blocks and the
// adjacent-block merge that follows it are all reproduced rather than
// re-derived; this is not an LCS and must not be rewritten as one, because
// the caller's threshold sits on the exact number difflib produces and the
// two algorithms do not agree.
//
// Three things about the Python are load-bearing and are the reason this
// file looks the way it does:
//
//   - **Python indexes `str` by CODE POINT.** `a[i]` is a one-character
//     str, `len(a)` is a count of code points, and `ratio()`'s denominator
//     is `len(a) + len(b)`. A port that indexes Go's `string` gets bytes,
//     and then every non-ASCII input has a longer sequence, a different
//     block decomposition and a different denominator. Everything here
//     works on `[]rune`, converted once at construction.
//
//   - **The adjacent-block merge is invisible to `ratio()`.** Collapsing
//     two adjacent blocks moves length from one triple to another and
//     leaves the sum alone, so no ratio can tell a working merge pass from
//     a deleted one. That is why MatchingBlocks is exported and
//     differentially tested in its own right: without it, a whole
//     paragraph of this file would have no witness.
//
//   - **isjunk is a real parameter, not dead weight.** The contract the
//     caller needs is `Ratio(a, b)`, which is `isjunk=None` — under which
//     `bjunk` is empty and find_longest_match's two junk-extension loops
//     can never execute. Rather than port four lines nothing can observe,
//     NewMatcher takes the predicate the way Python does, so those loops
//     have a CPython differential of their own.
//
// Deliberately out of scope: get_opcodes, get_grouped_opcodes,
// quick_ratio, real_quick_ratio, Differ, the HtmlDiff/ndiff surface, and
// set_seq1/set_seq2's `if a is self.a: return` identity cache — this port
// has no mutable-sequence lifecycle, every Matcher is built once.
package pydifflib

import "sort"

// Match is difflib's `Match = _namedtuple('Match', 'a b size')`: a[A:A+Size]
// equals b[B:B+Size].
type Match struct {
	A    int
	B    int
	Size int
}

// calculateRatio is difflib._calculate_ratio.
//
//	if length:
//	    return 2.0 * matches / length
//	return 1.0
//
// `if length:` is Python truthiness on an int — zero is the only falsey
// value a length can take, so the guard is `length != 0` and not
// `length > 0`; a negative length is unreachable, both here and there.
//
// The arithmetic is spelled in the Python's order: `2.0 * matches` first
// (an int-to-float conversion and a multiply), then a divide by length.
// Written as `matches / length * 2.0` it would round twice instead of
// once, and the caller compares the result to 0.85.
func calculateRatio(matches, length int) float64 {
	if length != 0 {
		return 2.0 * float64(matches) / float64(length)
	}
	return 1.0
}

// Matcher is SequenceMatcher, restricted to two `str` sequences.
type Matcher struct {
	// isjunk is SequenceMatcher's isjunk. nil is Python's None: absence,
	// which __chain_b's `if isjunk:` reads as falsey and skips. Note that
	// a nil func and a func that returns false everywhere are NOT the
	// same object here any more than they are there — the first skips the
	// purge loop entirely, the second runs it and finds nothing — and the
	// two are indistinguishable only because the loop has no other effect.
	isjunk   func(rune) bool
	a        []rune
	b        []rune
	autojunk bool

	// b2j maps each element of b to the ascending list of its indices in
	// b, minus junk and popular keys. A missing key is Python's
	// `b2j.get(a[i], nothing)` — an empty list — which Go's nil slice
	// gives for free.
	b2j map[rune][]int
	// b2jOrder is b2j's key INSERTION order, kept because Python's dict
	// has it and __chain_b iterates the dict twice (`for elt in
	// b2j.keys()` at 3.14's line 288, `for elt, idxs in b2j.items()` at
	// 299). Both loops only add to a set that is then used for deletion,
	// so the order is NOT observable in any result this package returns —
	// it is kept so that the loops read as the Python's loops and so a
	// future caller that does expose an ordering has it. Iterating a Go
	// map directly here would be a randomised order, which is worse than
	// either answer.
	b2jOrder []rune
	bjunk    map[rune]bool
	bpopular map[rune]bool

	// matchingBlocks is the `self.matching_blocks` cache. Python tests it
	// with `is not None`, so absence and "computed, and empty" are
	// distinct; nil is that absence. The computed value is never empty —
	// it always carries the (la, lb, 0) sentinel — so the distinction
	// costs nothing to maintain.
	matchingBlocks []Match

	// Instrumentation. These count how many times the four extension
	// loops in FindLongestMatch and the collapse arm of the merge pass
	// actually execute. Nothing in this package reads them; the
	// differential tests do, to prove the corpus reaches those lines at
	// all. A corpus that never runs a loop cannot tell a correct loop
	// from a deleted one, and three of the five were reached by no
	// obvious fixture.
	extendSteps     int
	junkExtendSteps int
	mergeSteps      int
}

// NewMatcher is SequenceMatcher(isjunk, a, b, autojunk).
//
// isjunk nil is Python's isjunk=None. autojunk mirrors the keyword's
// default only when the caller passes true; there is no defaulting here
// because Go has no keyword arguments, and a silent false would switch off
// the heuristic that decides the answer for every b of 200 or more.
func NewMatcher(isjunk func(rune) bool, a, b string, autojunk bool) *Matcher {
	m := &Matcher{
		isjunk:   isjunk,
		autojunk: autojunk,
		// set_seqs -> set_seq1 / set_seq2. The []rune conversion is where
		// "Python indexes by code point" is discharged: from here on
		// len() is a code-point count and indexing yields a code point.
		a: []rune(a),
		b: []rune(b),
	}
	m.chainB()
	return m
}

// chainB is SequenceMatcher.__chain_b.
func (m *Matcher) chainB() {
	b := m.b
	m.b2j = map[rune][]int{}
	b2j := m.b2j
	m.b2jOrder = nil

	// for i, elt in enumerate(b): b2j.setdefault(elt, []).append(i)
	//
	// Ranging a []rune yields the rune INDEX, unlike ranging a string,
	// which yields a byte offset. That is the whole reason a and b are
	// converted up front rather than ranged as strings here.
	for i, elt := range b {
		indices, ok := b2j[elt]
		if !ok {
			m.b2jOrder = append(m.b2jOrder, elt)
		}
		indices = append(indices, i)
		b2j[elt] = indices
	}

	// Purge junk elements.
	m.bjunk = map[rune]bool{}
	junk := m.bjunk
	isjunk := m.isjunk
	if isjunk != nil {
		for _, elt := range m.b2jOrder {
			if isjunk(elt) {
				junk[elt] = true
			}
		}
		for elt := range junk {
			delete(b2j, elt)
		}
	}

	// Purge popular elements that are not junk.
	m.bpopular = map[rune]bool{}
	popular := m.bpopular
	n := len(b)
	if m.autojunk && n >= 200 {
		// ntest = n // 100 + 1. Floor division on a non-negative int;
		// Go's / truncates toward zero, which agrees for n >= 200. It is
		// `n//100 + 1`, not `(n+1)//100` and not `n//100`: for n == 200
		// the threshold is 3 occurrences, and an element is popular at
		// STRICTLY more than that.
		ntest := n/100 + 1
		// Python iterates b2j.items() here, i.e. AFTER the junk keys were
		// deleted. Walking b2jOrder walks keys that may since have been
		// deleted, so the lookup's ok is what re-imposes that: a junk
		// element must not be reconsidered for popularity, because
		// difflib's sets are disjoint and bpopular is documented as
		// "nonjunk items in b treated as junk by the heuristic".
		for _, elt := range m.b2jOrder {
			idxs, ok := b2j[elt]
			if !ok {
				continue
			}
			if len(idxs) > ntest {
				popular[elt] = true
			}
		}
		for elt := range popular {
			delete(b2j, elt)
		}
	}
}

// FindLongestMatch is SequenceMatcher.find_longest_match(alo, ahi, blo, bhi).
//
// Python's ahi=None / bhi=None defaults (`if ahi is None: ahi = len(a)`)
// are not reproduced as a sentinel: every caller in this package passes
// all four, and an int has no None. MatchingBlocks passes len(a)/len(b)
// explicitly, which is what the defaults expand to.
func (m *Matcher) FindLongestMatch(alo, ahi, blo, bhi int) Match {
	a, b, b2j := m.a, m.b, m.b2j
	isbjunk := func(r rune) bool { return m.bjunk[r] }

	besti, bestj, bestsize := alo, blo, 0

	// find longest junk-free match
	// during an iteration of the loop, j2len[j] = length of longest
	// junk-free match ending with a[i-1] and b[j]
	//
	// Python's `nothing = []` has no counterpart: it is the default for
	// `b2j.get(a[i], nothing)`, and indexing a Go map with a missing key
	// already yields the nil slice, over which ranging is a no-op.
	j2len := map[int]int{}
	for i := alo; i < ahi; i++ {
		// j2lenget is bound to the PREVIOUS row before newj2len is
		// created, so the lookup below reads the row for a[i-1] while
		// the row for a[i] is being filled. Two maps, not one: writing
		// into the map being read is the classic way to port this wrong,
		// and it silently lengthens runs of a repeated character.
		prev := j2len
		newj2len := map[int]int{}
		for _, j := range b2j[a[i]] {
			// a[i] matches b[j]
			if j < blo {
				continue
			}
			if j >= bhi {
				// break, not continue: b2j's index lists are ascending,
				// so everything after this is also out of window.
				break
			}
			// k = newj2len[j] = j2lenget(j-1, 0) + 1
			//
			// The `.get(..., 0)` default is a real branch in Python and
			// a real value here: j2len only ever holds lengths >= 1, so
			// a missing key and a stored 0 cannot collide, and Go's
			// zero-value lookup is the same answer.
			k := prev[j-1] + 1
			newj2len[j] = k
			if k > bestsize {
				besti, bestj, bestsize = i-k+1, j-k+1, k
			}
		}
		j2len = newj2len
	}

	// Extend the best by non-junk elements on each end. In particular,
	// "popular" non-junk elements aren't in b2j, which greatly speeds the
	// inner loop above, but also means "the best" match so far doesn't
	// contain any junk *or* popular non-junk elements.
	for besti > alo && bestj > blo &&
		!isbjunk(b[bestj-1]) &&
		a[besti-1] == b[bestj-1] {
		besti, bestj, bestsize = besti-1, bestj-1, bestsize+1
		m.extendSteps++
	}
	for besti+bestsize < ahi && bestj+bestsize < bhi &&
		!isbjunk(b[bestj+bestsize]) &&
		a[besti+bestsize] == b[bestj+bestsize] {
		bestsize++
		m.extendSteps++
	}

	// Now that we have a wholly interesting match (albeit possibly
	// empty!), we may as well suck up the matching junk on each side of
	// it too.
	for besti > alo && bestj > blo &&
		isbjunk(b[bestj-1]) &&
		a[besti-1] == b[bestj-1] {
		besti, bestj, bestsize = besti-1, bestj-1, bestsize+1
		m.junkExtendSteps++
	}
	for besti+bestsize < ahi && bestj+bestsize < bhi &&
		isbjunk(b[bestj+bestsize]) &&
		a[besti+bestsize] == b[bestj+bestsize] {
		bestsize = bestsize + 1
		m.junkExtendSteps++
	}

	return Match{A: besti, B: bestj, Size: bestsize}
}

// MatchingBlocks is SequenceMatcher.get_matching_blocks.
//
// The returned slice is the cache itself, as Python returns the cached
// list; callers must not mutate it.
func (m *Matcher) MatchingBlocks() []Match {
	if m.matchingBlocks != nil {
		return m.matchingBlocks
	}
	la, lb := len(m.a), len(m.b)

	// The explicit queue is difflib's, replacing what reads most
	// naturally as recursion; it is kept because the shape is part of
	// what was ported, even though `queue.pop()` being LIFO is not
	// observable: each entry names a disjoint window and is resolved
	// independently of the others, and the collected blocks are sorted
	// before anything looks at them.
	queue := [][4]int{{0, la, 0, lb}}
	var matchingBlocks []Match
	for len(queue) > 0 {
		q := queue[len(queue)-1]
		queue = queue[:len(queue)-1]
		alo, ahi, blo, bhi := q[0], q[1], q[2], q[3]
		x := m.FindLongestMatch(alo, ahi, blo, bhi)
		i, j, k := x.A, x.B, x.Size
		// a[alo:i] vs b[blo:j] unknown
		// a[i:i+k] same as b[j:j+k]
		// a[i+k:ahi] vs b[j+k:bhi] unknown
		if k != 0 { // if k is 0, there was no matching block
			matchingBlocks = append(matchingBlocks, x)
			if alo < i && blo < j {
				queue = append(queue, [4]int{alo, i, blo, j})
			}
			if i+k < ahi && j+k < bhi {
				queue = append(queue, [4]int{i + k, ahi, j + k, bhi})
			}
		}
	}
	// matching_blocks.sort() on a list of namedtuples is a lexicographic
	// sort on (a, b, size). Stability is not at stake: two triples that
	// compare equal on all three fields are the same triple.
	sort.Slice(matchingBlocks, func(x, y int) bool {
		p, q := matchingBlocks[x], matchingBlocks[y]
		if p.A != q.A {
			return p.A < q.A
		}
		if p.B != q.B {
			return p.B < q.B
		}
		return p.Size < q.Size
	})

	// It's possible that we have adjacent equal blocks in the
	// matching_blocks list now. Collapse them.
	//
	// This pass cannot change the sum of the sizes, so ratio() is blind
	// to it — see the package doc.
	i1, j1, k1 := 0, 0, 0
	var nonAdjacent []Match
	for _, blk := range matchingBlocks {
		i2, j2, k2 := blk.A, blk.B, blk.Size
		// Is this block adjacent to i1, j1, k1?
		if i1+k1 == i2 && j1+k1 == j2 {
			k1 += k2
			m.mergeSteps++
		} else {
			// Not adjacent. Remember the first block (k1 == 0 means it's
			// the dummy we started with), and make the second block the
			// new block to compare against.
			if k1 != 0 {
				nonAdjacent = append(nonAdjacent, Match{A: i1, B: j1, Size: k1})
			}
			i1, j1, k1 = i2, j2, k2
		}
	}
	if k1 != 0 {
		nonAdjacent = append(nonAdjacent, Match{A: i1, B: j1, Size: k1})
	}

	nonAdjacent = append(nonAdjacent, Match{A: la, B: lb, Size: 0})
	m.matchingBlocks = nonAdjacent
	return m.matchingBlocks
}

// Ratio is SequenceMatcher.ratio.
//
//	matches = sum(triple[-1] for triple in self.get_matching_blocks())
//	return _calculate_ratio(matches, len(self.a) + len(self.b))
//
// The sum runs over EVERY triple including the (la, lb, 0) sentinel, which
// contributes nothing; dropping it from the loop would be the same number
// and a different program, so it stays in.
func (m *Matcher) Ratio() float64 {
	matches := 0
	for _, triple := range m.MatchingBlocks() {
		matches += triple.Size
	}
	return calculateRatio(matches, len(m.a)+len(m.b))
}

// Ratio is `difflib.SequenceMatcher(None, a, b).ratio()`.
//
// isjunk=None and autojunk left at its default True — the two arguments
// difflib's own get_close_matches passes, and the ones the caller of this
// package needs, since it thresholds the result at 0.85.
func Ratio(a, b string) float64 {
	return NewMatcher(nil, a, b, true).Ratio()
}
