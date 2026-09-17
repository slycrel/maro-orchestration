package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/judgment"
	spine "github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/secrets"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// storeKey resolves a named bearer token from the SECRETS STORE — the one
// place a credential lives on this machine (docs/SECRETS_DESIGN.md). The
// value goes to the provider and is never printed, logged, or held where
// a record could reach it.
func storeKey(name string) (string, error) {
	v, ok, err := secrets.Open().Get(name)
	if err != nil {
		return "", err
	}
	if !ok {
		return "", fmt.Errorf("%s is not in the secrets store (%s)", name, secrets.Open().StorePath())
	}
	return v, nil
}

// hostedSpec is the hosted tier's binding: defaults from the
// hosted-free ladder, overridden by flags so another host is a config
// change.
type hostedSpec struct{ url, model, keyName string }

// jevKey resolves the TypeSafe bearer token from the SECRETS STORE — the
// one place a credential lives on this machine (docs/SECRETS_DESIGN.md).
// The value is returned to the provider and never printed, logged, or
// held anywhere a record could reach.
func jevKey() (string, error) {
	v, ok, err := secrets.Open().Get(judgment.JevKeyName)
	if err != nil {
		return "", err
	}
	if !ok {
		return "", fmt.Errorf("%s is not in the secrets store (%s)", judgment.JevKeyName, secrets.Open().StorePath())
	}
	return v, nil
}

// buildProviders wires the wire providers by name. The llm arm needs no
// entry: it is whichever backend the attempt judges on.
func buildProviders(names []string, pcdURL string, hosted hostedSpec) map[string]judgment.Provider {
	ps := map[string]judgment.Provider{}
	for _, n := range names {
		switch n {
		case judgment.ProviderJev:
			ps[n] = judgment.NewJev(jevKey)
		case judgment.ProviderPCD:
			ps[n] = judgment.NewPCD(pcdURL)
		case judgment.ProviderHosted:
			ps[n] = judgment.NewHosted(hosted.url, hosted.model, hosted.keyName, storeKey)
		}
	}
	return ps
}

// splitNames parses a comma list of provider names.
func splitNames(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

// cmdJudgment is the judgment surface: report (what the shadow arm
// recorded), ask (a manual probe), replay (a labelled corpus through
// several providers).
func cmdJudgment(args []string, out, errw io.Writer) error {
	if len(args) < 1 {
		return fmt.Errorf("judgment needs a subcommand: report [--json] | ask --provider p --state-file f --questions-file q | replay --corpus f --providers a,b")
	}
	switch args[0] {
	case "report":
		return cmdJudgmentReport(args[1:], out)
	case "ask":
		return cmdJudgmentAsk(args[1:], out, errw)
	case "replay":
		return cmdJudgmentReplay(args[1:], out, errw)
	}
	return fmt.Errorf("unknown judgment subcommand %q (report | ask | replay)", args[0])
}

func cmdJudgmentReport(args []string, out io.Writer) error {
	asJSON := len(args) > 0 && args[0] == "--json"
	return withJournal(out, func(a *workspace.Announced, j *journal.Journal, st *thought.Store) error {
		s, err := judgment.Summarize(j.Production(), j.Control())
		if err != nil {
			return err
		}
		if asJSON {
			b, err := json.MarshalIndent(s, "", "  ")
			if err != nil {
				return err
			}
			fmt.Fprintln(out, string(b))
			return nil
		}
		s.Render(out)
		return nil
	})
}

// cmdJudgmentAsk is the manual probe: a state file and a questions file
// in, one provider's answers out. It goes through invoke.Shell like every
// other call, so the probe is recorded too.
func cmdJudgmentAsk(args []string, out, errw io.Writer) error {
	provider, statePath, questionsPath, pcdURL, model := judgment.ProviderLLM, "", "", judgment.DefaultPCDURL, ""
	backend := "subprocess"
	var hosted hostedSpec
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--provider":
			i++
			if i < len(args) {
				provider = args[i]
			}
		case "--state-file":
			i++
			if i < len(args) {
				statePath = args[i]
			}
		case "--questions-file":
			i++
			if i < len(args) {
				questionsPath = args[i]
			}
		case "--pcd-url":
			i++
			if i < len(args) {
				pcdURL = args[i]
			}
		case "--model":
			i++
			if i < len(args) {
				model = args[i]
			}
		case "--backend":
			i++
			if i < len(args) {
				backend = args[i]
			}
		case "--hosted-url":
			i++
			if i < len(args) {
				hosted.url = args[i]
			}
		case "--hosted-model":
			i++
			if i < len(args) {
				hosted.model = args[i]
			}
		case "--hosted-key":
			i++
			if i < len(args) {
				hosted.keyName = args[i]
			}
		}
	}
	if statePath == "" || questionsPath == "" {
		return fmt.Errorf("judgment ask needs --state-file and --questions-file (the questions file is a {\"<id>\": {\"type\": ..., \"instructions\": ...}} object)")
	}
	state, err := os.ReadFile(statePath)
	if err != nil {
		return err
	}
	questions, err := os.ReadFile(questionsPath)
	if err != nil {
		return err
	}
	p, err := probeProvider(provider, backend, model, pcdURL, hosted)
	if err != nil {
		return err
	}
	req, err := probeRequest(p, state, questions)
	if err != nil {
		return err
	}
	return withJournal(out, func(a *workspace.Announced, j *journal.Journal, st *thought.Store) error {
		sh := &invoke.Shell{J: j, Store: st}
		res, o, err := judgment.Ask(context.Background(), sh, p, invoke.PurposeShadowJudge, req, judgment.DefaultTimeout)
		if o != nil {
			fmt.Fprintf(errw, "invocation %s (%s, %d ms)\n", o.Invocation, o.Terminal, o.Usage.WallMillis)
		}
		if err != nil {
			return err
		}
		b, err := judgment.EncodeResponse(res)
		if err != nil {
			return err
		}
		fmt.Fprintln(out, string(b))
		return nil
	})
}

// probeRequest assembles a Request from the two files: the state is any
// JSON, the questions are the wire question objects.
func probeRequest(p judgment.Provider, state, questions []byte) (judgment.Request, error) {
	body := fmt.Sprintf(`{"model":%q,"state":%s,"questions":%s}`, modelFor(p), strings.TrimSpace(string(state)), strings.TrimSpace(string(questions)))
	return judgment.DecodeRequest([]byte(body))
}

func modelFor(p judgment.Provider) string {
	if m := p.Capabilities().Model; strings.TrimSpace(m) != "" {
		return m
	}
	return p.Name()
}

// probeProvider builds one provider for a probe: a wire provider, or the
// llm arm over a real backend.
func probeProvider(name, backend, model, pcdURL string, hosted hostedSpec) (judgment.Provider, error) {
	switch name {
	case judgment.ProviderJev:
		return judgment.NewJev(jevKey), nil
	case judgment.ProviderPCD:
		return judgment.NewPCD(pcdURL), nil
	case judgment.ProviderHosted:
		return judgment.NewHosted(hosted.url, hosted.model, hosted.keyName, storeKey), nil
	case judgment.ProviderLLM:
		switch backend {
		case "subprocess":
			s, err := invoke.NewSubprocess(model)
			if err != nil {
				return nil, err
			}
			return &judgment.LLM{B: s}, nil
		case "scripted":
			return nil, fmt.Errorf("the scripted backend answers nothing useful to a probe")
		}
		return nil, fmt.Errorf("unknown backend %q", backend)
	}
	return nil, fmt.Errorf("unknown provider %q (known: %v)", name, judgment.Known())
}

// ---- replay: a labelled corpus through several providers ----------------

type replayCase struct {
	ID         string `json:"id"`
	StepText   string `json:"step_text"`
	Result     string `json:"result"`
	ExpectPass bool   `json:"expect_pass"`
}

type replayTally struct {
	provider  string
	n         int
	agree     int
	falsePass int
	falseFail int
	refused   int
	latency   []int64
	rows      []string
}

// cmdJudgmentReplay asks each named provider the STEP judge's question
// about every labelled case and prints how each arm's answers line up
// with the labels. A provider that cannot be reached is skipped with an
// honest line; nothing here changes any record the engine decides on.
func cmdJudgmentReplay(args []string, out, errw io.Writer) error {
	corpus, providers, pcdURL, model, limit := "", "llm", judgment.DefaultPCDURL, "", 0
	var hosted hostedSpec
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--corpus":
			i++
			if i < len(args) {
				corpus = args[i]
			}
		case "--providers":
			i++
			if i < len(args) {
				providers = args[i]
			}
		case "--pcd-url":
			i++
			if i < len(args) {
				pcdURL = args[i]
			}
		case "--model":
			i++
			if i < len(args) {
				model = args[i]
			}
		case "--limit":
			i++
			if i < len(args) {
				fmt.Sscanf(args[i], "%d", &limit)
			}
		case "--hosted-url":
			i++
			if i < len(args) {
				hosted.url = args[i]
			}
		case "--hosted-model":
			i++
			if i < len(args) {
				hosted.model = args[i]
			}
		case "--hosted-key":
			i++
			if i < len(args) {
				hosted.keyName = args[i]
			}
		}
	}
	if corpus == "" {
		return fmt.Errorf("judgment replay needs --corpus <validation_cases.json>")
	}
	raw, err := os.ReadFile(corpus)
	if err != nil {
		return err
	}
	var file struct {
		Cases []replayCase `json:"cases"`
	}
	if err := json.Unmarshal(raw, &file); err != nil {
		return fmt.Errorf("corpus: %w", err)
	}
	cases := file.Cases
	if limit > 0 && limit < len(cases) {
		cases = cases[:limit]
	}
	if len(cases) == 0 {
		return fmt.Errorf("corpus %s holds no cases", corpus)
	}
	names := splitNames(providers)
	return withJournal(out, func(a *workspace.Announced, j *journal.Journal, st *thought.Store) error {
		fmt.Fprintf(out, "judgment replay: %d cases from %s\n", len(cases), corpus)
		for _, name := range names {
			p, err := probeProvider(name, "subprocess", model, pcdURL, hosted)
			if err != nil {
				fmt.Fprintf(out, "\n%s: skipped — %v\n", name, err)
				continue
			}
			t := &replayTally{provider: name}
			for _, c := range cases {
				// a replayed corpus case has no execution record: the judge is told so, never shown an empty one
				req := spine.StepJudgeRequest(modelFor(p), []byte(c.StepText), c.StepText, []byte(c.Result), invoke.TerminalComplete, false, invoke.EvidenceUnavailable)
				sh := &invoke.Shell{J: j, Store: st}
				start := time.Now()
				res, o, err := judgment.Ask(context.Background(), sh, p, invoke.PurposeShadowJudge, req, judgment.DefaultTimeout)
				ms := time.Since(start).Milliseconds()
				if o != nil {
					t.latency = append(t.latency, ms)
				}
				if err != nil {
					t.refused++
					t.rows = append(t.rows, fmt.Sprintf("  %-32s expect %-5v  REFUSED  %v", c.ID, c.ExpectPass, firstLine(err.Error())))
					continue
				}
				ans, _ := res.One()
				pass := ans.Choice == string(spine.StepDoneOK)
				t.n++
				mark := "agree"
				switch {
				case pass == c.ExpectPass:
					t.agree++
				case pass && !c.ExpectPass:
					t.falsePass++
					mark = "FALSE PASS"
				default:
					t.falseFail++
					mark = "FALSE FAIL"
				}
				t.rows = append(t.rows, fmt.Sprintf("  %-32s expect %-5v  said %-8s conf %.2f  %-10s %4d ms", c.ID, c.ExpectPass, ans.Choice, ans.Confidence, mark, ms))
			}
			renderTally(out, t)
		}
		return nil
	})
}

func renderTally(out io.Writer, t *replayTally) {
	fmt.Fprintf(out, "\n%s: %d answered, %d refused\n", t.provider, t.n, t.refused)
	if t.n > 0 {
		fmt.Fprintf(out, "  agreement with the label: %d/%d (%.0f%%)  false PASS %d  false FAIL %d\n",
			t.agree, t.n, 100*float64(t.agree)/float64(t.n), t.falsePass, t.falseFail)
	}
	if len(t.latency) > 0 {
		l := append([]int64{}, t.latency...)
		sort.Slice(l, func(i, j int) bool { return l[i] < l[j] })
		fmt.Fprintf(out, "  latency: median %d ms, max %d ms\n", l[len(l)/2], l[len(l)-1])
	}
	for _, r := range t.rows {
		fmt.Fprintln(out, r)
	}
}
