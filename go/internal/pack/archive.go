// Archive I/O for .maropack.tar.gz files — physical form shared with
// src/pack.py: pack.json + REVIEW.md + artifacts/<workspace-relative...>,
// entries stamped mtime=0 (deterministic contents).
package pack

import (
	"archive/tar"
	"compress/gzip"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	PackFormat      = 1
	ScrubberVersion = 1
	ArchiveSuffix   = ".maropack.tar.gz"
)

// tarEntry keeps insertion order — the archive lists artifacts in the
// order the exporter gathered them, same as Python's ordered dict.
type tarEntry struct {
	name string
	data []byte
}

func writeArchive(path string, entries []tarEntry) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	gz := gzip.NewWriter(f)
	tw := tar.NewWriter(gz)
	for _, e := range entries {
		hdr := &tar.Header{
			Name: e.name, Size: int64(len(e.data)),
			Mode: 0o644, ModTime: time.Unix(0, 0),
		}
		if err := tw.WriteHeader(hdr); err != nil {
			return err
		}
		if _, err := tw.Write(e.data); err != nil {
			return err
		}
	}
	if err := tw.Close(); err != nil {
		return err
	}
	if err := gz.Close(); err != nil {
		return err
	}
	return f.Close()
}

// readArchive returns every regular-file member of the archive by name.
func readArchive(path string) (map[string][]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	members := map[string][]byte{}
	for {
		hdr, err := tr.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("read %s: %w", path, err)
		}
		if hdr.Typeflag != tar.TypeReg {
			continue
		}
		data, err := io.ReadAll(tr)
		if err != nil {
			return nil, fmt.Errorf("read %s member %s: %w", path, hdr.Name, err)
		}
		members[hdr.Name] = data
	}
	return members, nil
}

// decodeManifest parses pack.json with UseNumber so numeric literals
// survive verbatim into the canonical digest (see canonical.go).
func decodeManifest(raw []byte) (map[string]any, error) {
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	var m map[string]any
	if err := dec.Decode(&m); err != nil {
		return nil, fmt.Errorf("pack.json: %w", err)
	}
	return m, nil
}

// manifestArtifacts pulls the artifacts list as []map[string]any,
// tolerating absence (empty pack).
func manifestArtifacts(manifest map[string]any) []map[string]any {
	arr, _ := manifest["artifacts"].([]any)
	var out []map[string]any
	for _, e := range arr {
		if m, ok := e.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

// reviewCompanionPath ports _review_companion_path: the loose
// <name>.REVIEW.md beside the archive.
func reviewCompanionPath(packPath string) string {
	name := filepath.Base(packPath)
	name = strings.TrimSuffix(name, ArchiveSuffix)
	return filepath.Join(filepath.Dir(packPath), name+".REVIEW.md")
}
