package runs

import (
	"crypto/sha1"
	"fmt"
)

// Nickname is the deterministic two-word name Python appends to every
// run-dir. The same handle id always yields the same nickname; sha1
// spreads across the adjective/noun space evenly regardless of how
// handle ids are distributed.
//
// This exists because of a divergence that was NAMED in this package's
// doc comment as harmless and was not. The Go port wrote its run dirs as
// the bare handle id, on the reasoning that "readers glob
// runs/*/metadata.json, so naming is not contract". Two of the three
// readers do glob. The third is runs.run_dir(handle_id), which builds
// the path by NAME — and it is the first thing resolve_run_dir tries,
// the thing create_run_dir uses to decide whether a run dir already
// exists, and the thing every resume path goes through.
//
// So the bare-id dir was reachable from Python only by falling through
// to the ref index or the legacy scan, and a Python create_run_dir for a
// handle id a Go run had already started would MISS the existing
// directory and make a second one beside it — two run dirs for one run,
// each holding half the metadata, in the shared workspace the whole port
// exists to interoperate with.
//
// The lesson is narrower than "port everything": it is that a naming
// scheme is a lookup key whenever any reader reconstructs the name
// instead of listing the directory, and the doc comment that waved this
// off had checked the readers it could see.
func Nickname(handleID string) string {
	if handleID == "" {
		return "unset-run"
	}
	digest := sha1.Sum([]byte(handleID))
	return fmt.Sprintf("%s-%s",
		adjectives[int(digest[0])%len(adjectives)],
		nouns[int(digest[1])%len(nouns)])
}

// The two word lists, in Python's order. ORDER IS CONTRACT here, not
// style: the nickname is an index into these tuples, so reordering
// either list silently renames every future run dir while leaving every
// existing one in place — the same handle id would resolve to two
// different paths on the two runtimes. They are spelled out rather than
// sorted or generated for that reason.
var adjectives = [...]string{
	"amber", "azure", "brisk", "calm", "clever", "cobalt", "crisp",
	"dapper", "dusky", "eager", "fierce", "frosty", "gentle", "gilded",
	"glassy", "golden", "hardy", "humble", "icy", "jaunty", "keen",
	"lively", "lucid", "merry", "misty", "noble", "nimble", "olive",
	"patient", "plucky", "quick", "quiet", "rapid", "ruby", "rustic",
	"silent", "silver", "sleek", "spry", "stout", "sturdy", "sunny",
	"swift", "tawny", "tidy", "vivid", "warm", "wily", "witty", "zesty",
}

var nouns = [...]string{
	"alder", "ash", "badger", "beacon", "birch", "brook", "cedar",
	"comet", "crane", "delta", "echo", "ember", "falcon", "ferret",
	"finch", "forge", "glen", "harbor", "haven", "heron", "ibis",
	"jasper", "kestrel", "lantern", "ledger", "lichen", "magpie",
	"marsh", "meadow", "moss", "nettle", "oak", "orchard", "otter",
	"pebble", "pine", "quartz", "raven", "ridge", "river", "saffron",
	"shore", "spruce", "thicket", "thorn", "tundra", "vale", "wren",
	"yarrow", "zephyr",
}
