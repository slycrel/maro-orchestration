// Canonical hashing shared with src/pack.py. The payload digest is
// recomputed INDEPENDENTLY by whichever runtime imports a pack, over the
// artifact metadata parsed from pack.json — so the canonical JSON encoding
// here must be byte-identical to Python's
// json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
// or no Go-sealed pack would ever verify in Python (and vice versa).
package pack

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"strconv"
	"strings"
)

// pyCanonJSON renders a decoded-JSON value exactly as Python's canonical
// json.dumps does: sorted keys, no separators whitespace, raw UTF-8
// (ensure_ascii=False), short escapes for the JSON control set, \u00xx
// for other control chars. Numbers must arrive as json.Number so the
// source literal survives verbatim (a float64 round-trip could turn 5
// into 5.0 or shift a float's shortest form — both would silently change
// the digest).
func pyCanonJSON(v any, sb *strings.Builder) error {
	switch t := v.(type) {
	case nil:
		sb.WriteString("null")
	case bool:
		if t {
			sb.WriteString("true")
		} else {
			sb.WriteString("false")
		}
	case json.Number:
		sb.WriteString(t.String())
	case int:
		sb.WriteString(strconv.Itoa(t))
	case string:
		writePyString(t, sb)
	case []any:
		sb.WriteByte('[')
		for i, e := range t {
			if i > 0 {
				sb.WriteByte(',')
			}
			if err := pyCanonJSON(e, sb); err != nil {
				return err
			}
		}
		sb.WriteByte(']')
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		sb.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				sb.WriteByte(',')
			}
			writePyString(k, sb)
			sb.WriteByte(':')
			if err := pyCanonJSON(t[k], sb); err != nil {
				return err
			}
		}
		sb.WriteByte('}')
	default:
		return fmt.Errorf("canonical JSON: unsupported type %T (float64 means "+
			"a decode path skipped UseNumber)", v)
	}
	return nil
}

// writePyString matches CPython's json string escaping with
// ensure_ascii=False: ", \, and the C0 control set only; everything else
// (including non-ASCII) ships as raw UTF-8.
func writePyString(s string, sb *strings.Builder) {
	sb.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			sb.WriteString(`\"`)
		case '\\':
			sb.WriteString(`\\`)
		case '\n':
			sb.WriteString(`\n`)
		case '\r':
			sb.WriteString(`\r`)
		case '\t':
			sb.WriteString(`\t`)
		case '\b':
			sb.WriteString(`\b`)
		case '\f':
			sb.WriteString(`\f`)
		default:
			if r < 0x20 {
				fmt.Fprintf(sb, `\u%04x`, r)
			} else {
				sb.WriteRune(r)
			}
		}
	}
	sb.WriteByte('"')
}

// CanonicalJSON is pyCanonJSON as a convenience returning the bytes.
func CanonicalJSON(v any) ([]byte, error) {
	var sb strings.Builder
	if err := pyCanonJSON(v, &sb); err != nil {
		return nil, err
	}
	return []byte(sb.String()), nil
}

func sha256Text(text string) string {
	h := sha256.Sum256([]byte(text))
	return hex.EncodeToString(h[:])
}

func sha256File(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// payloadSHA256 ports pack._payload_sha256: a length-framed digest over
// each artifact's canonical metadata, path, and payload, in sorted-path
// order. files is keyed by the manifest "path" (with the artifacts/
// prefix), exactly as Python keys it.
func payloadSHA256(artifacts []map[string]any, files map[string][]byte) (string, error) {
	byPath := map[string]map[string]any{}
	for _, a := range artifacts {
		p, _ := a["path"].(string)
		byPath[p] = a
	}
	paths := make([]string, 0, len(byPath))
	for p := range byPath {
		paths = append(paths, p)
	}
	sort.Strings(paths)

	h := sha256.New()
	for _, p := range paths {
		metadata, err := CanonicalJSON(byPath[p])
		if err != nil {
			return "", err
		}
		raw := files[p]
		h.Write([]byte(strconv.Itoa(len(metadata))))
		h.Write([]byte{0})
		h.Write(metadata)
		h.Write([]byte{0})
		h.Write([]byte(p))
		h.Write([]byte{0})
		h.Write([]byte(strconv.Itoa(len(raw))))
		h.Write([]byte{0})
		h.Write(raw)
		h.Write([]byte{0})
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
