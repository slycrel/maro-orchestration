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
	"unicode/utf8"
)

const (
	PackFormat      = 1
	ScrubberVersion = 1
	ArchiveSuffix   = ".maropack.tar.gz"
)

// Decompression bounds on untrusted archives (adversarial round
// 2026-08-22: a KB-sized gzip bomb would otherwise be materialized in
// full BEFORE any hash gate runs). Go-only hardening — Python's tarfile
// path is still unbounded, named in PORT.md as a cross-runtime residual.
// Vars, not consts, so tests can exercise the refusal without allocating
// the real ceiling.
var (
	maxArchiveMemberBytes int64 = 64 << 20  // one member's content, decompressed
	maxArchiveTotalBytes  int64 = 256 << 20 // all member content, decompressed
	maxArchiveMembers           = 4096
	// Allowance for everything that is NOT member content: tar headers,
	// padding, and PAX/GNU meta records the stdlib consumes INSIDE
	// tr.Next() without ever returning them (r3 2026-08-22, Skeptic HIGH:
	// each meta record is 1MiB-capped by archive/tar, but a run of
	// consecutive records is unbounded in count — invisible to the entry
	// cap). The decompressor-level cap below bounds them categorically.
	maxArchiveHeaderBytes int64 = 16 << 20
)

var errDecompressionCeiling = errors.New(
	"archive exceeds the decompressed-bytes ceiling — refused")

// cappedReader hard-bounds bytes drawn from the decompressor. It sits
// BETWEEN gzip and tar, so every byte tar touches — headers, PAX/GNU
// meta records, padding, file content — counts, regardless of how the
// stdlib attributes it internally. The per-member/total checks in
// readArchive give precise errors; this is the categorical backstop.
type cappedReader struct {
	r         io.Reader
	remaining int64
}

func (c *cappedReader) Read(p []byte) (int, error) {
	if c.remaining <= 0 {
		return 0, errDecompressionCeiling
	}
	if int64(len(p)) > c.remaining {
		p = p[:c.remaining]
	}
	n, err := c.r.Read(p)
	c.remaining -= int64(n)
	return n, err
}

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
// It REFUSES (never OOMs) archives over the decompression bounds, and
// refuses any member that is not valid UTF-8 — Python's .decode("utf-8")
// crashes loudly on such bytes, where Go's string conversion would
// silently substitute U+FFFD and mangle content the digests then bless
// (adversarial round 2026-08-22). Every member of a pack is text by
// construction.
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
	tr := tar.NewReader(&cappedReader{
		r: gz, remaining: maxArchiveTotalBytes + maxArchiveHeaderBytes})
	members := map[string][]byte{}
	var total int64
	entries := 0
	for {
		hdr, err := tr.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("read %s: %w", path, err)
		}
		// EVERY header counts against the entry cap, and non-regular
		// entries are refused outright — a legitimate pack only ever holds
		// regular files, and r2 (2026-08-22, both lenses) showed the r1
		// bounds had a total bypass: dir/symlink headers hit a bare
		// `continue` BEFORE any cap, so millions of tiny repetitive
		// headers decompress-looped unbounded.
		entries++
		if entries > maxArchiveMembers {
			return nil, fmt.Errorf(
				"refused %s: more than %d members", path, maxArchiveMembers)
		}
		if hdr.Typeflag != tar.TypeReg {
			return nil, fmt.Errorf(
				"refused %s: member %s is not a regular file (typeflag %q)",
				path, hdr.Name, hdr.Typeflag)
		}
		if _, dup := members[hdr.Name]; dup {
			// Last-wins would let a raw-archive viewer show a reviewer
			// different bytes than the ones the digest blesses.
			return nil, fmt.Errorf(
				"refused %s: duplicate member %s", path, hdr.Name)
		}
		// LimitReader at cap+1: reading a full cap+1 bytes proves the
		// member is over the cap without materializing the rest.
		data, err := io.ReadAll(io.LimitReader(tr, maxArchiveMemberBytes+1))
		if err != nil {
			return nil, fmt.Errorf("read %s member %s: %w", path, hdr.Name, err)
		}
		if int64(len(data)) > maxArchiveMemberBytes {
			return nil, fmt.Errorf(
				"refused %s: member %s exceeds %d decompressed bytes",
				path, hdr.Name, maxArchiveMemberBytes)
		}
		total += int64(len(data))
		if total > maxArchiveTotalBytes {
			return nil, fmt.Errorf(
				"refused %s: archive exceeds %d decompressed bytes",
				path, maxArchiveTotalBytes)
		}
		if !utf8.Valid(data) {
			return nil, fmt.Errorf(
				"refused %s: member %s is not valid UTF-8", path, hdr.Name)
		}
		members[hdr.Name] = data
	}
	return members, nil
}

// decodeManifest parses pack.json with UseNumber so numeric literals
// survive verbatim into the canonical digest (see canonical.go).
// Lone-surrogate \uXXXX escapes are refused BEFORE decoding: Go's
// decoder would silently turn them into U+FFFD while Python either keeps
// the lone surrogate in its str and then crashes encoding the digest, or
// refuses — either way Python never accepts what Go would have quietly
// rewritten (adversarial round 2026-08-22).
func decodeManifest(raw []byte) (map[string]any, error) {
	if err := refuseLoneSurrogates(raw); err != nil {
		return nil, fmt.Errorf("pack.json: %w", err)
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	var m map[string]any
	if err := dec.Decode(&m); err != nil {
		return nil, fmt.Errorf("pack.json: %w", err)
	}
	return m, nil
}

// refuseLoneSurrogates scans raw JSON text for \uD800–\uDFFF escapes
// that do not form a valid high+low surrogate pair. It tracks the
// backslash escape state so \\u1234 (a literal backslash) is not
// misread as an escape.
func refuseLoneSurrogates(raw []byte) error {
	hexVal := func(at int) (int, bool) {
		if at+4 > len(raw) {
			return 0, false
		}
		v := 0
		for _, b := range raw[at : at+4] {
			v <<= 4
			switch {
			case b >= '0' && b <= '9':
				v |= int(b - '0')
			case b >= 'a' && b <= 'f':
				v |= int(b-'a') + 10
			case b >= 'A' && b <= 'F':
				v |= int(b-'A') + 10
			default:
				return 0, false
			}
		}
		return v, true
	}
	for i := 0; i < len(raw); i++ {
		if raw[i] != '\\' {
			continue
		}
		if i+1 >= len(raw) {
			break
		}
		next := raw[i+1]
		if next != 'u' {
			i++ // consume the escaped char (covers \\, \", \n, ...)
			continue
		}
		v, ok := hexVal(i + 2)
		if !ok {
			i++ // malformed escape; the JSON decoder will refuse it
			continue
		}
		switch {
		case v >= 0xDC00 && v <= 0xDFFF:
			return fmt.Errorf("lone low surrogate \\u%04x — refused", v)
		case v >= 0xD800 && v <= 0xDBFF:
			// Must be immediately followed by \uDC00–\uDFFF.
			lowAt := i + 6
			if lowAt+1 >= len(raw) || raw[lowAt] != '\\' || raw[lowAt+1] != 'u' {
				return fmt.Errorf("lone high surrogate \\u%04x — refused", v)
			}
			low, ok := hexVal(lowAt + 2)
			if !ok || low < 0xDC00 || low > 0xDFFF {
				return fmt.Errorf("lone high surrogate \\u%04x — refused", v)
			}
			i = lowAt + 5 // past the low surrogate's escape
			continue
		}
		i += 5 // past this \uXXXX
	}
	return nil
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
