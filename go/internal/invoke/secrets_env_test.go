package invoke

import (
	"strings"
	"testing"
	"time"
)

// The secrets seams on the subprocess backend (docs/SECRETS_DESIGN.md):
// Env reaches a TOOL-BEARING child and never a tool-less one; injected
// values are redacted from the response and the transcript, longest
// first; AfterTools fires once per tool-bearing call.
func TestSubprocessSecretsSeams(t *testing.T) {
	dir := t.TempDir()
	echo := writeFake(t, dir, "echo-env", `cat >/dev/null
printf '{"type":"result","subtype":"success","is_error":false,"result":"user=%s pw=%s drop=%s","usage":{"input_tokens":1,"output_tokens":1}}\n' "$YAHOO_USER" "$YAHOO_APP_PASSWORD" "$MARO_SECRETS_DROP"
`)
	sh, _ := newShell(t)
	fired := 0
	b := &Subprocess{Bin: echo, Model: "sonnet", DefaultTimeout: 10 * time.Second,
		Env:        []string{"YAHOO_USER=u-long-value", "YAHOO_APP_PASSWORD=u-long", "MARO_SECRETS_DROP=/ws/drop/secrets-derived.env"},
		Redact:     map[string]string{"YAHOO_USER": "u-long-value", "YAHOO_APP_PASSWORD": "u-long"},
		AfterTools: func() { fired++ }}
	out, err := sh.Invoke(ctxBg, b, execReq("what is in your env"), nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := string(out.Response); got != "user=[REDACTED:YAHOO_USER] pw=[REDACTED:YAHOO_APP_PASSWORD] drop=/ws/drop/secrets-derived.env" {
		t.Fatalf("response %q", got)
	}
	st := foldOne(t, sh, out.Invocation)
	if st.Terminal.Transcript == nil {
		t.Fatal("no transcript kept")
	}
	if tr, err := sh.Store.Get(*st.Terminal.Transcript); err != nil || strings.Contains(string(tr), "u-long") {
		t.Fatalf("transcript leaks a value (%v): %s", err, tr)
	}
	if fired != 1 {
		t.Fatalf("AfterTools fired %d", fired)
	}
	// tool-less: no env, no redaction pass, no AfterTools
	req := Request{Purpose: PurposeJudge, Prompt: []byte("judge"), Tools: false}
	out2, err := sh.Invoke(ctxBg, b, req, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := string(out2.Response); got != "user= pw= drop=" {
		t.Fatalf("a tool-less call must not see the injection: %q", got)
	}
	if fired != 1 {
		t.Fatalf("AfterTools fired on a tool-less call: %d", fired)
	}
}
