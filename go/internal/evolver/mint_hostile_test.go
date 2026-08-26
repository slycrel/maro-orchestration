package evolver

import (
	"context"
	"os"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

func hostileGoPatterns(t *testing.T, reply string) []string {
	t.Helper()
	ws := t.TempDir()
	seedMintOutcomes(t, ws)
	rep := Run(context.Background(), ws, record.New(ws), nil,
		&llm.Fake{Script: []string{reply}}, RunOptions{})
	return rep.FailurePatterns
}

// TestMintHostileReplies drives thirty hostile model replies through the
// mint site and requires CPython and the port to agree on every one:
// duplicate keys, lone surrogates, NaN and Infinity literals, markdown
// fences, think blocks, prose around the JSON, two top-level objects,
// confidence as a bool / a list / 1e400 / a spaced string.
//
// It is GATED, and the gate is the point. Each case spawns a CPython
// interpreter that imports the whole maro stack and runs a full evolver
// cycle; thirty of those is fourteen minutes, which is over the default
// ten-minute package timeout. Ungated it does not fail — it takes
// `go test ./...` down with a goroutine dump instead of a diff, which is
// strictly worse than a red test because nothing in the output says what
// broke. (Found 2026-08-26, having done exactly that.)
//
// Last run green: 2026-08-26, 840s, all thirty cases agreeing.
//
// Run it with:
//
//	MARO_SLOW_DIFF=1 go test ./internal/evolver/ -run Hostile -timeout 45m
//
// The real fix is to batch the CPython side into ONE interpreter the way
// the metrics and introspect differentials do — one probe over a list of
// cases rather than one probe per case. That is a rewrite of the seeding,
// because each reply needs its own store, and it is not worth doing
// carelessly: a batched probe that silently reuses one workspace would let
// each reply seed the next one's read, and `outcomes_analyzed` counts
// exactly that file.
func TestMintHostileReplies(t *testing.T) {
	if os.Getenv("MARO_SLOW_DIFF") == "" {
		t.Skip("slow differential (~14 min, 30 CPython interpreters): set " +
			"MARO_SLOW_DIFF=1 to run it")
	}
	cases := []struct {
		name  string
		reply string
	}{
		{"duplicate keys in a suggestion", `{"suggestions":[{"category":"a","category":"b","target":"t","confidence":0.4}]}`},
		{"duplicate suggestions key", `{"suggestions":[{"category":"a","confidence":0.4}],"suggestions":[{"category":"z","confidence":0.4}]}`},
		{"deeply nested pattern", `{"suggestions":[{"category":"c","pattern":{"a":[{"b":[{"c":[1,2,{"d":null}]}]}]},"confidence":0.4}]}`},
		{"unicode fields", `{"suggestions":[{"category":"café ☃","target":"中文","suggestion":"é","pattern":"😀","confidence":0.4}]}`},
		{"confidence 1e400", `{"suggestions":[{"category":"a","confidence":1e400}]}`},
		{"confidence -0", `{"suggestions":[{"category":"a","confidence":-0}]}`},
		{"confidence -0.0", `{"suggestions":[{"category":"a","confidence":-0.0}]}`},
		{"confidence negative", `{"suggestions":[{"category":"a","confidence":-3}]}`},
		{"confidence string", `{"suggestions":[{"category":"a","confidence":"0.8"}]}`},
		{"confidence string with space", `{"suggestions":[{"category":"a","confidence":"  0.8  "}]}`},
		{"confidence string underscore", `{"suggestions":[{"category":"a","confidence":"1_0"}]}`},
		{"confidence bool", `{"suggestions":[{"category":"a","confidence":true},{"category":"b","confidence":false}]}`},
		{"confidence huge int", `{"suggestions":[{"category":"a","confidence":10000000000000000000000000}]}`},
		{"confidence list", `{"suggestions":[{"category":"a","confidence":[1]}]}`},
		{"control chars", "{\"suggestions\":[{\"category\":\"a\\u0000b\",\"target\":\"c\\u001fd\",\"suggestion\":\"\\t x \\n\",\"pattern\":\"p\\u0007q\",\"confidence\":0.4}]}"},
		{"lone surrogate escape", "{\"suggestions\":[{\"category\":\"a\\ud800b\",\"confidence\":0.4}]}"},
		{"empty suggestions array", `{"failure_patterns":["p"],"suggestions":[]}`},
		{"suggestions as object", `{"suggestions":{"0":{"category":"a","confidence":0.4}}}`},
		{"markdown fences", "```json\n{\"suggestions\":[{\"category\":\"fenced\",\"confidence\":0.4}]}\n```"},
		{"think block", "<think>musing {\"decoy\":1}</think>\n{\"suggestions\":[{\"category\":\"real\",\"confidence\":0.4}]}"},
		{"failure_patterns mixed", `{"failure_patterns":["a",1,null,{"x":1},"b"],"suggestions":[{"category":"a","confidence":0.4}]}`},
		{"failure_patterns not a list", `{"failure_patterns":"nope","suggestions":[{"category":"a","confidence":0.4}]}`},
		{"nan confidence literal", `{"suggestions":[{"category":"a","confidence":NaN}]}`},
		{"infinity confidence literal", `{"suggestions":[{"category":"a","confidence":Infinity}]}`},
		{"expected_signal nested", `{"suggestions":[{"category":"a","confidence":0.4,"expected_signal":[{"metric":"m","direction":"down","nested":{"b":1,"a":2}}]}]}`},
		{"expected_signal not list of dict", `{"suggestions":[{"category":"a","confidence":0.4,"expected_signal":{"metric":"m"}}]}`},
		{"prose then json", "Here is my analysis.\n{\"suggestions\":[{\"category\":\"a\",\"confidence\":0.4}]}\nThanks!"},
		{"two objects", `{"suggestions":[{"category":"first","confidence":0.4}]} {"suggestions":[{"category":"second","confidence":0.4}]}`},
		{"top level array", `[{"suggestions":[{"category":"a","confidence":0.4}]}]`},
		{"tab and nbsp in text", "{\"suggestions\":[{\"category\":\"a\",\"suggestion\":\"\\u00a0 x \\u2028 y\",\"confidence\":0.4}]}"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			want := runMintOnCPython(t, c.reply)
			got := runMintOnGo(t, c.reply)
			compareMintRows(t, got, want.Rows)
			// also compare patterns
			gp := hostileGoPatterns(t, c.reply)
			if len(gp) != len(want.Patterns) {
				t.Errorf("patterns: go=%#v py=%#v", gp, want.Patterns)
			} else {
				for i := range gp {
					if gp[i] != want.Patterns[i] {
						t.Errorf("pattern %d: go=%q py=%q", i, gp[i], want.Patterns[i])
					}
				}
			}
		})
	}
}
