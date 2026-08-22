// Seal — stamp review.human_reviewed=true plus tamper-evidence hashes.
// Ports pack.seal_pack. Not a signature or proof of who authored the
// archive; it proves the REVIEW.md a human read matches what ships.
package pack

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

// Seal refuses without explicit confirmation (the CLI's --yes). The
// sha256 stamped is of the REVIEW.md a human actually read — the loose
// companion if present (pre-seal edits count), else the archived copy.
func Seal(packPath string, confirmed bool) (map[string]any, error) {
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
	var artifactOrder []string
	for name, data := range members {
		if name == "pack.json" || name == "REVIEW.md" {
			continue
		}
		artifactBytes[name] = data
	}
	// Preserve the archive's member order on rewrite (map iteration is
	// random; the manifest's artifact list is the stable order).
	for _, a := range manifestArtifacts(manifest) {
		if p, ok := a["path"].(string); ok {
			if _, present := artifactBytes[p]; present {
				artifactOrder = append(artifactOrder, p)
			}
		}
	}
	for name := range artifactBytes {
		found := false
		for _, p := range artifactOrder {
			if p == name {
				found = true
				break
			}
		}
		if !found {
			artifactOrder = append(artifactOrder, name)
		}
	}

	companion := reviewCompanionPath(packPath)
	reviewText := archivedReview
	if raw, err := os.ReadFile(companion); err == nil {
		reviewText = string(raw)
	}

	payloadSHA, err := payloadSHA256(manifestArtifacts(manifest), artifactBytes)
	if err != nil {
		return nil, err
	}
	marker := fmt.Sprintf("Reviewed payload SHA-256: `%s`", payloadSHA)
	oldMarker := "\n\n---\n\nReviewed payload SHA-256: `"
	if at := strings.LastIndex(reviewText, oldMarker); at >= 0 &&
		strings.HasSuffix(strings.TrimSpace(reviewText[at+len(oldMarker):]), "`") {
		reviewText = reviewText[:at]
	}
	// The digest lives in the human-reviewed artifact as well as pack.json.
	// A payload+manifest swap that retains the reviewed copy therefore fails.
	reviewText = strings.TrimRight(reviewText, " \t\n") +
		fmt.Sprintf("\n\n---\n\n%s\n", marker)

	manifest["review"] = map[string]any{
		"human_reviewed":         true,
		"reviewed_at":            nowISO(),
		"review_manifest_sha256": sha256Text(reviewText),
		"review_payload_sha256":  payloadSHA,
	}

	manifestJSON, err := json.MarshalIndent(manifest, "", "  ")
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
	if err := os.WriteFile(companion, []byte(reviewText), 0o644); err != nil {
		return nil, err
	}
	return manifest, nil
}
