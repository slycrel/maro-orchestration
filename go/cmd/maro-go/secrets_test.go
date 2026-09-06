package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/secrets"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// fakeSopsSh keeps sops' contract on a store whose values are ENC[FAKE:v]:
// -d needs the identity (else 128); --set rewrites one name in place.
const fakeSopsSh = `#!/bin/sh
[ -f "$SOPS_AGE_KEY_FILE" ] || { echo nokey >&2; exit 128; }
case "$1" in
  -d) f=$4; printf '{'; sep=''
      while IFS= read -r l; do case "$l" in sops_*|'#'*|'') continue;; esac
        k=${l%%=*}; v=${l#*=}; v=${v#ENC[FAKE:}; v=${v%]}
        printf '%s"%s":"%s"' "$sep" "$k" "$v"; sep=','
      done < "$f"; printf '}';;
  --set) spec=$2; f=$3
      k=$(printf '%s' "$spec" | sed 's/^\["\([^"]*\)"\].*/\1/')
      v=$(printf '%s' "$spec" | sed 's/^\[[^]]*\] *"\(.*\)"$/\1/')
      grep -v "^$k=" "$f" | grep -v '^sops_' > "$f.tmp"
      printf '%s=ENC[FAKE:%s]\n' "$k" "$v" >> "$f.tmp"
      grep '^sops_' "$f" >> "$f.tmp"; mv "$f.tmp" "$f";;
  *) echo "fake sops: $*" >&2; exit 2;;
esac
`

// fakeClaudeSh saves the prompt it was handed, drops a derived credential
// where the frame said to, and answers with what it saw in its env.
const fakeClaudeSh = `#!/bin/sh
cat >> "$FAKE_PROMPT_OUT"; printf "\n----\n" >> "$FAKE_PROMPT_OUT"
if [ -n "$MARO_SECRETS_DROP" ]; then printf 'MINTED_TOKEN=tok-1\n' > "$MARO_SECRETS_DROP"; fi
printf '{"type":"result","subtype":"success","is_error":false,"result":"user=%s","usage":{"input_tokens":1,"output_tokens":1}}\n' "$YAHOO_USER"
`

func secretsFixture(t *testing.T, policy string) (dir string) {
	t.Helper()
	bin := t.TempDir()
	for name, body := range map[string]string{"sops": fakeSopsSh, "claude": fakeClaudeSh} {
		if err := os.WriteFile(filepath.Join(bin, name), []byte(body), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	dir = t.TempDir()
	t.Setenv(secrets.EnvDir, dir)
	store := "MARO_SECRETS_STORE=ENC[FAKE:1]\nYAHOO_USER=ENC[FAKE:u-secret]\nNVIDIA_API_KEY=ENC[FAKE:n-secret]\nsops_age__list_0__map_recipient=age1thisbox\nsops_version=3.13.3\n"
	for name, body := range map[string]string{secrets.StoreName: store, secrets.IdentityName: "# public key: age1thisbox\nAGE-SECRET-KEY-1FAKE\n", secrets.PolicyName: policy} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	meta := map[string]secrets.Meta{"NVIDIA_API_KEY": {Origin: "operator", Created: "2026-09-06T10:00:00Z", Service: "nvidia"}}
	b, _ := json.Marshal(meta)
	if err := os.WriteFile(filepath.Join(dir, secrets.MetaName), b, 0o600); err != nil {
		t.Fatal(err)
	}
	return dir
}

func TestCLISecretsVerbs(t *testing.T) {
	secretsFixture(t, "YAHOO_*\n")
	var out, errw bytes.Buffer
	if code := run([]string{"secrets", "list"}, &out, &errw); code != 0 {
		t.Fatalf("list exit %d: %s", code, errw.String())
	}
	got := out.String()
	if !strings.Contains(got, "* YAHOO_USER") || !strings.Contains(got, "  NVIDIA_API_KEY (operator, 2026-09-06, nvidia)") || strings.Contains(got, "secret") {
		t.Fatalf("list:\n%s", got)
	}
	out.Reset()
	if code := run([]string{"secrets", "check", "--json"}, &out, &errw); code != 0 {
		t.Fatalf("check exit %d: %s", code, errw.String())
	}
	var r secrets.Report
	if err := json.Unmarshal(out.Bytes(), &r); err != nil || r.OpensHere == nil || !*r.OpensHere || strings.Join(r.Injectable, ",") != "YAHOO_USER" {
		t.Fatalf("check %v: %s", err, out.String())
	}
	out.Reset()
	if code := run([]string{"secrets", "get", "YAHOO_USER"}, &out, &errw); code != 0 || strings.TrimSpace(out.String()) != "u-secret" {
		t.Fatalf("get exit %d: %q %s", code, out.String(), errw.String())
	}
	out.Reset()
	errw.Reset()
	if code := run([]string{"secrets", "get", "NOPE"}, &out, &errw); code == 0 || !strings.Contains(errw.String(), "no value for NOPE") {
		t.Fatalf("missing name must fail: %d %s", code, errw.String())
	}
	// no store: check fails, list says so
	t.Setenv(secrets.EnvDir, t.TempDir())
	out.Reset()
	errw.Reset()
	if code := run([]string{"secrets", "check"}, &out, &errw); code == 0 || !strings.Contains(out.String(), "store:         none") {
		t.Fatalf("absent store: %d %s %s", code, out.String(), errw.String())
	}
}

// A NOW run on the subprocess backend: the execute frame carries the
// presence block, the policy's names ride into the worker's env (and are
// redacted from what comes back), the drop path is announced and the
// dropped credential lands in the store as maro-derived.
func TestCLINowInjectsAndIngestsSecrets(t *testing.T) {
	dir := secretsFixture(t, "YAHOO_*\n")
	t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
	promptOut := filepath.Join(t.TempDir(), "prompt.txt")
	t.Setenv("FAKE_PROMPT_OUT", promptOut)
	var out, errw bytes.Buffer
	if code := run([]string{"now", "--backend", "subprocess", "--fresh", "who am I logged in as"}, &out, &errw); code != 0 {
		t.Fatalf("now exit %d: %s %s", code, out.String(), errw.String())
	}
	prompt, err := os.ReadFile(promptOut)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"## Secrets", "Injected into your environment as variables: YAHOO_USER.", "Not injected (you are on the host, same user as the operator): MARO_SECRETS_STORE, NVIDIA_API_KEY.", "NVIDIA_API_KEY (operator, 2026-09-06, nvidia)", "($" + secrets.DropEnv + ")"} {
		if !strings.Contains(string(prompt), want) {
			t.Fatalf("frame lacks %q:\n%s", want, prompt)
		}
	}
	if strings.Contains(string(prompt), "u-secret") || strings.Contains(string(prompt), "n-secret") {
		t.Fatal("a value leaked into the prompt")
	}
	// the worker's answer named the injected value: redacted on the way back
	if strings.Contains(out.String(), "u-secret") || !strings.Contains(out.String(), "[REDACTED:YAHOO_USER]") {
		t.Fatalf("response not redacted:\n%s", out.String())
	}
	if !strings.Contains(errw.String(), "secrets: step derived MINTED_TOKEN (stored, origin=maro)") {
		t.Fatalf("ingest not reported:\n%s", errw.String())
	}
	sec := secrets.New(dir)
	if v, ok, err := sec.Get("MINTED_TOKEN"); err != nil || !ok || v != "tok-1" {
		t.Fatalf("dropped value not stored: %q %v %v", v, ok, err)
	}
	if m := sec.Meta()["MINTED_TOKEN"]; m.Origin != secrets.OriginMaro || m.Source != "drop" {
		t.Fatalf("meta %+v", m)
	}
	if entries, _ := filepath.Glob(filepath.Join(os.Getenv(workspace.EnvOverride), "drop", "*")); len(entries) != 0 {
		t.Fatalf("drop file survived: %v", entries)
	}
}
