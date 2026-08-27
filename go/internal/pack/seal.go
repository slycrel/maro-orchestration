// Seal — stamp review.human_reviewed=true plus tamper-evidence hashes.
// Ports pack.seal_pack. Not a signature or proof of who authored the
// archive; it proves the REVIEW.md a human read matches what ships.
package pack

import (
	"fmt"
	"os"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Seal refuses without explicit confirmation (the CLI's --yes). The
// sha256 stamped is of the REVIEW.md a human actually read — the loose
// companion if present (pre-seal edits count), else the archived copy.
func Seal(packPath string, confirmed bool) (pyval.Obj, error) {
	if _, err := os.Stat(packPath); err != nil {
		return nil, fmt.Errorf("no such pack: %s", packPath)
	}
	if !confirmed {
		return nil, fmt.Errorf(
			"seal refused: human review not confirmed — read REVIEW.md, then pass --yes")
	}

	members, err := readArchive(packPath)
	if err != nil {
		return nil, err
	}
	manifestRaw, ok := members["pack.json"]
	if !ok {
		return nil, fmt.Errorf("seal: %s has no pack.json", packPath)
	}
	manifest, err := decodeManifest(manifestRaw)
	if err != nil {
		return nil, err
	}
	archivedReview := string(members["REVIEW.md"])

	artifactBytes := map[string][]byte{}
	for name, data := range members {
		if name == "pack.json" || name == "REVIEW.md" {
			continue
		}
		artifactBytes[name] = data
	}
	// The manifest's artifacts list and the archive's members must be a
	// bijection BEFORE a human's review gets stamped onto the result: a
	// missing member would otherwise seal a truncated pack (payloadSHA256
	// also refuses, belt and braces), and an extra member would ride the
	// sealed archive outside the digest and outside REVIEW.md
	// (adversarial round 2026-08-22; Python still carries extras — named
	// in PORT.md). The manifest's artifact order is the stable rewrite
	// order (map iteration is random).
	var artifactOrder []string
	listed := map[string]bool{}
	for _, a := range manifestArtifacts(manifest) {
		raw, _ := a.Get("path")
		p, ok := raw.(string)
		if !ok {
			return nil, fmt.Errorf("seal refused: manifest artifact without a path")
		}
		if listed[p] {
			return nil, fmt.Errorf("seal refused: manifest lists %q more than once", p)
		}
		listed[p] = true
		if _, present := artifactBytes[p]; !present {
			return nil, fmt.Errorf(
				"seal refused: manifest names %q but the archive has no such member", p)
		}
		artifactOrder = append(artifactOrder, p)
	}
	for name := range artifactBytes {
		if !listed[name] {
			return nil, fmt.Errorf(
				"seal refused: archive member %q is not listed in the manifest", name)
		}
	}

	companion := reviewCompanionPath(packPath)
	reviewText := archivedReview
	// `companion.read_text(encoding="utf-8") if companion.exists() else
	// archived_review`. This is the DOCUMENTED path where a human edited
	// REVIEW.md before sealing, so it is exactly where an editor that writes
	// CRLF lands — and the result is hashed into review_manifest_sha256. An
	// untranslated read seals the same reviewed text to a different digest.
	if _, serr := os.Stat(companion); serr == nil {
		text, err := pyval.ReadText(companion)
		if err != nil {
			return nil, err
		}
		reviewText = text
	}

	payloadSHA, err := payloadSHA256(manifestArtifacts(manifest), artifactBytes)
	if err != nil {
		return nil, err
	}
	marker := fmt.Sprintf("Reviewed payload SHA-256: `%s`", payloadSHA)
	oldMarker := "\n\n---\n\nReviewed payload SHA-256: `"
	// pytext.Strip, not TrimSpace, and pytext.TrimRight, not
	// TrimRight(" \t\n"): both spellings decide whether the OLD marker is
	// removed and where the text ends, and the result is hashed into
	// review_manifest_sha256. A REVIEW.md whose tail carries an information
	// separator (Python strips it, TrimSpace does not) or a \r / \v / \f /
	// U+00A0 (Python's bare rstrip() strips it, a three-cutset TrimRight
	// does not) seals to two different digests, and the pack then fails
	// verification on whichever runtime did not seal it.
	if at := strings.LastIndex(reviewText, oldMarker); at >= 0 &&
		strings.HasSuffix(pytext.Strip(reviewText[at+len(oldMarker):]), "`") {
		reviewText = reviewText[:at]
	}
	// The digest lives in the human-reviewed artifact as well as pack.json.
	// A payload+manifest swap that retains the reviewed copy therefore fails.
	reviewText = pytext.TrimRight(reviewText) +
		fmt.Sprintf("\n\n---\n\n%s\n", marker)

	// `manifest["review"] = {...}` (pack.py:457). Assigning to an
	// EXISTING key does not move it in a Python dict, so `review` keeps
	// its ordinal — sixth, between `artifacts` and `trust_policy` — and
	// the sealed pack.json differs from the unsealed one in exactly the
	// four values below. Obj.Set is that assignment: in place for a key
	// that is there, appended only for one that is not. A port that
	// rebuilt the manifest here, or that appended the replacement, would
	// move `review` to the tail and rewrite every line after it.
	manifest.Set("review", pyval.Obj{
		{Key: "human_reviewed", Val: true},
		{Key: "reviewed_at", Val: nowISO()},
		{Key: "review_manifest_sha256", Val: sha256Text(reviewText)},
		{Key: "review_payload_sha256", Val: payloadSHA},
	})

	manifestJSON, err := manifestBytes(manifest)
	if err != nil {
		return nil, err
	}
	entries := []tarEntry{
		{"pack.json", append(manifestJSON, '\n')},
		{"REVIEW.md", []byte(reviewText)},
	}
	for _, name := range artifactOrder {
		entries = append(entries, tarEntry{name, artifactBytes[name]})
	}
	if err := writeArchive(packPath, entries); err != nil {
		return nil, err
	}
	if err := os.WriteFile(companion, []byte(reviewText), 0o666); err != nil {
		return nil, err
	}
	return manifest, nil
}
