package contracts

import (
	"fmt"
	"os"
	"os/exec"
	"reflect"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

const generatorID = "maro-go contracts gen v1"

// GenerateAll derives one Generated per registered kind. Pure: no I/O.
func GenerateAll(sourceRef string) []Generated {
	specs := record.All()
	out := make([]Generated, 0, len(specs))
	for _, s := range specs {
		out = append(out, generate(s, sourceRef))
	}
	return out
}

func generate(s record.Spec, sourceRef string) Generated {
	g := Generated{
		Kind: string(s.Kind), Envelope: s.Envelope.String(),
		Schema: fmt.Sprintf("%s/%d", s.Kind, s.Version), GoType: s.Type.String(),
		SourceRef: sourceRef, Generator: generatorID,
	}
	g.Fields = fieldsOf(s.Type, "", false)
	return g
}

var (
	headerType = reflect.TypeOf(record.Header{})
	markerTys  = map[reflect.Type]bool{
		reflect.TypeOf(record.ProductionRecord{}): true, reflect.TypeOf(record.ControlRecord{}): true, reflect.TypeOf(record.ExperimentalRecord{}): true,
	}
)

func fieldsOf(t reflect.Type, prefix string, fromHeader bool) []GeneratedField {
	var out []GeneratedField
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		if markerTys[f.Type] {
			continue // envelope markers are not wire fields
		}
		tag := f.Tag.Get("json")
		name, opts, _ := strings.Cut(tag, ",")
		if name == "-" {
			continue
		}
		if name == "" {
			name = f.Name
		}
		if f.Anonymous && f.Type == headerType {
			// header fields are flattened under their wire prefix
			sub := fieldsOf(headerType, prefix+name+".", true)
			for i := range sub {
				sub[i].Embedded = true
			}
			out = append(out, sub...)
			continue
		}
		omit := strings.Contains(opts, "omitempty") || f.Type.Kind() == reflect.Pointer || f.Type.Kind() == reflect.Slice || f.Type.Kind() == reflect.Map
		isThought := strings.HasSuffix(f.Type.String(), "thought.Ref") || (f.Name == "Hash" && strings.Contains(t.String(), "Thought"))
		out = append(out, GeneratedField{
			Name: prefix + f.Name, Wire: prefix + name, GoType: f.Type.String(),
			Omittable: omit, IsThought: isThought, FromHeader: fromHeader,
		})
	}
	return out
}

// SourceRef returns the current git HEAD or "unknown".
func SourceRef() string {
	cmd := exec.Command("git", "rev-parse", "--short", "HEAD")
	cmd.Stderr = nil
	out, err := cmd.Output()
	if err != nil {
		return "unknown"
	}
	return strings.TrimSpace(string(out))
}

// WriteGenerated writes every generated file into dir (creating it).
func WriteGenerated(dir Dir, gens []Generated) error {
	if err := os.MkdirAll(string(dir), 0o755); err != nil {
		return err
	}
	for _, g := range gens {
		if err := writeJSON(dir.genPath(g.Kind), g); err != nil {
			return err
		}
	}
	return nil
}

// Drift compares fresh generation against the committed files, ignoring the
// source ref (which legitimately moves every commit). A non-empty result means
// the contract changed and the regenerated file must be committed in the same
// change — the diff is the review.
func Drift(dir Dir, fresh []Generated) ([]string, error) {
	var drift []string
	for _, g := range fresh {
		have, err := dir.ReadGenerated(g.Kind)
		if err != nil {
			drift = append(drift, fmt.Sprintf("%s: no committed generated file (%v)", g.Kind, err))
			continue
		}
		a, b := *have, g
		a.SourceRef, b.SourceRef = "", ""
		if fmt.Sprintf("%+v", a) != fmt.Sprintf("%+v", b) {
			drift = append(drift, fmt.Sprintf("%s: committed generated contract differs from the Go type", g.Kind))
		}
	}
	return drift, nil
}
