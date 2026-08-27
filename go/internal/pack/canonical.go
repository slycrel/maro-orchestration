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

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pyCanonJSON renders a decoded-JSON value exactly as Python's canonical
// json.dumps does: sorted keys, no separators whitespace, raw UTF-8
// (ensure_ascii=False), short escapes for the JSON control set, \u00xx
// for other control chars.
//
// Numbers: json.Number literals are emitted VERBATIM, which matches
// Python only because Python re-emits what json.dumps itself produced —
// its json.loads parses to int/float and dumps re-normalizes ("5.00"
// would become "5.0" there, but no exporter in either runtime ever
// writes a non-canonical literal into the hashed metadata, and pack.json
// is only ever machine-written by json.dumps/MarshalIndent). For a
// hand-crafted manifest carrying a non-canonical spelling the two
// runtimes' digests DIVERGE — deliberately unhandled, because the
// failure direction is a digest mismatch, i.e. refusal (pinned by
// TestCanonicalJSONNonCanonicalNumberLiteral). json.Number (not float64)
// is still required so a decode can't silently reshape a literal both
// sides agreed on.
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
	case pyval.List:
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
	// An ORDERED object still hashes SORTED. sort_keys=True is what the
	// two runtimes agreed on for the digest, and it is the opposite
	// decision from pack.json's insertion-order render — the same value
	// is written two ways on purpose, one for the human-readable
	// manifest and one for the hash. Making the manifest ordered must
	// not quietly turn the digest into an insertion-order digest, which
	// would break every pack either runtime has already sealed.
	case pyval.Obj:
		keys := make([]string, 0, len(t))
		byKey := make(map[string]any, len(t))
		for _, f := range t {
			if _, dup := byKey[f.Key]; !dup {
				keys = append(keys, f.Key)
			}
			// Last-wins, like the dict CPython hashes: decodeOrdered
			// already collapses duplicates, so this is a floor rather
			// than a live case.
			byKey[f.Key] = f.Val
		}
		sort.Strings(keys)
		sb.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				sb.WriteByte(',')
			}
			writePyString(k, sb)
			sb.WriteByte(':')
			if err := pyCanonJSON(byKey[k], sb); err != nil {
				return err
			}
		}
		sb.WriteByte('}')
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
//
// A manifest path with no matching file is an ERROR, never a zero-length
// payload — Python fails the same way by KeyError, and a Go map's nil
// zero-value would otherwise let Seal stamp a "clean" digest over a
// truncated archive (adversarial round 2026-08-22, Skeptic HIGH).
func payloadSHA256(artifacts []pyval.Obj, files map[string][]byte) (string, error) {
	byPath := map[string]pyval.Obj{}
	for _, a := range artifacts {
		p := a.GetString("path")
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
		raw, present := files[p]
		if !present {
			return "", fmt.Errorf(
				"payload digest: manifest names %q but no such file was provided", p)
		}
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
