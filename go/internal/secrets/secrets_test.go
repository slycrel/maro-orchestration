package secrets

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// fakeSops is the Exec seam: the store file's values are `ENC[FAKE:<v>]`
// (reversible, so the test never needs a real key), `-d` needs the
// identity file to exist (else sops' exit 128), `--set` rewrites in place.
func fakeSops(t *testing.T) func(bin string, args []string, env []string) ([]byte, int, error) {
	t.Helper()
	return func(bin string, args []string, env []string) ([]byte, int, error) {
		keyFile := ""
		for _, kv := range env {
			if strings.HasPrefix(kv, "SOPS_AGE_KEY_FILE=") {
				keyFile = strings.TrimPrefix(kv, "SOPS_AGE_KEY_FILE=")
			}
		}
		if _, err := os.Stat(keyFile); err != nil {
			return nil, exitNoKey, fmt.Errorf("sops exit 128: no key")
		}
		path := args[len(args)-1]
		b, err := os.ReadFile(path)
		if err != nil {
			return nil, 100, fmt.Errorf("sops exit 100: no file")
		}
		lines := strings.Split(strings.TrimRight(string(b), "\n"), "\n")
		switch args[0] {
		case "-d":
			out := map[string]string{}
			for _, l := range lines {
				k, v, ok := strings.Cut(l, "=")
				if !ok || strings.HasPrefix(k, "sops_") {
					continue
				}
				out[k] = strings.TrimSuffix(strings.TrimPrefix(v, "ENC[FAKE:"), "]")
			}
			j, _ := json.Marshal(out)
			return j, 0, nil
		case "--set":
			spec := args[1]
			var key []string
			_ = json.Unmarshal([]byte(spec[:strings.Index(spec, "]")+1]), &key)
			var val string
			_ = json.Unmarshal([]byte(strings.TrimSpace(spec[strings.Index(spec, "]")+1:])), &val)
			var names, meta []string
			for _, l := range lines {
				if strings.HasPrefix(l, "sops_") {
					meta = append(meta, l)
				} else if !strings.HasPrefix(l, key[0]+"=") {
					names = append(names, l)
				}
			}
			names = append(names, key[0]+"=ENC[FAKE:"+val+"]")
			return nil, 0, os.WriteFile(path, []byte(strings.Join(append(names, meta...), "\n")+"\n"), 0o600)
		}
		return nil, 2, fmt.Errorf("fake sops: %v", args)
	}
}

func fixture(t *testing.T, values map[string]string, policy string) *Store {
	t.Helper()
	dir := t.TempDir()
	s := New(dir)
	s.Lookup = func(string) (string, error) { return "/fake/sops", nil }
	s.Exec = fakeSops(t)
	var lines []string
	for _, k := range []string{"MARO_SECRETS_STORE", "YAHOO_USER", "YAHOO_APP_PASSWORD", "NVIDIA_API_KEY"} {
		if v, ok := values[k]; ok {
			lines = append(lines, k+"=ENC[FAKE:"+v+"]")
		}
	}
	lines = append(lines, "sops_age__list_0__map_recipient=age1thisbox", "sops_mac=ENC[x]", "sops_version=3.13.3")
	if err := os.WriteFile(s.StorePath(), []byte(strings.Join(lines, "\n")+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(s.IdentityPath(), []byte("# public key: age1thisbox\nAGE-SECRET-KEY-1FAKE\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if policy != "" {
		if err := os.WriteFile(s.PolicyPath(), []byte(policy), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return s
}

func TestParsersMatchThePythonReading(t *testing.T) {
	text := "ALPHA=ENC[AES256_GCM,data:x,type:str]\n#ENC[c,type:comment]\n\nBETA=ENC[...]\nEMPTY=\nsops_age__list_0__map_recipient=age1abc\nsops_mac=ENC[...]\nALPHA=dup\nnovalue\n"
	if got := ParseNames(text); strings.Join(got, ",") != "ALPHA,BETA,EMPTY" {
		t.Fatalf("names %v", got)
	}
	if got := ParsePolicy("# c\nYAHOO_*  # mail\n\nNVIDIA_API_KEY\nYAHOO_*\n"); strings.Join(got, ",") != "YAHOO_*,NVIDIA_API_KEY" {
		t.Fatalf("policy %v", got)
	}
	names := []string{"NVIDIA_API_KEY", "YAHOO_USER", "yahoo_lower", "YAHOO_APP_PASSWORD", "OTHER"}
	if got := Injectable(names, []string{"YAHOO_*", "OTHER"}); strings.Join(got, ",") != "YAHOO_USER,YAHOO_APP_PASSWORD,OTHER" {
		t.Fatalf("injectable %v", got)
	}
	if Injectable(names, nil) != nil {
		t.Fatal("no policy must inject nothing")
	}
	for _, bad := range []string{"", "1ABC", "A-B", "A B", "A=B", "$X"} {
		if ValidName(bad) {
			t.Fatalf("%q accepted", bad)
		}
	}
	if !ValidName("YAHOO_APP_PASSWORD") {
		t.Fatal("valid name refused")
	}
	if got := ParseDotenv("# c\nA=\"x y\"\nB='z'\nC=q=r\n\nnovalue\n"); len(got) != 3 || got[0][1] != "x y" || got[1][1] != "z" || got[2][1] != "q=r" {
		t.Fatalf("dotenv %v", got)
	}
}

func TestNoStoreIsQuiet(t *testing.T) {
	s := New(t.TempDir())
	if s.Present() || s.Names() != nil || s.Presence(nil, "", "") != "" {
		t.Fatal("an absent store must be silent")
	}
	if v, err := s.Load(); v != nil || err != nil {
		t.Fatalf("load %v %v", v, err)
	}
	inj, err := s.Inject()
	if err != nil || len(inj.Env) != 0 {
		t.Fatalf("inject %+v %v", inj, err)
	}
	r := s.Check()
	if r.Store != "" || r.OpensHere != nil || len(r.Names) != 0 {
		t.Fatalf("%+v", r)
	}
}

func TestNamesWithoutTheKeyAndValuesWithIt(t *testing.T) {
	s := fixture(t, map[string]string{"MARO_SECRETS_STORE": "1", "YAHOO_USER": "u", "NVIDIA_API_KEY": "n"}, "")
	if got := strings.Join(s.Names(), ","); got != "MARO_SECRETS_STORE,YAHOO_USER,NVIDIA_API_KEY" {
		t.Fatalf("names %s", got)
	}
	if got := s.Recipients(); len(got) != 1 || got[0] != "age1thisbox" {
		t.Fatalf("recipients %v", got)
	}
	v, ok, err := s.Get("YAHOO_USER")
	if err != nil || !ok || v != "u" {
		t.Fatalf("get %q %v %v", v, ok, err)
	}
	if r := s.Check(); r.OpensHere == nil || !*r.OpensHere || len(r.Names) != 3 {
		t.Fatalf("%+v", r)
	}
	// without the identity: names still, values refused with the named cause
	if err := os.Remove(s.IdentityPath()); err != nil {
		t.Fatal(err)
	}
	s.cache = nil
	if _, err := s.Load(); err == nil || !strings.Contains(err.Error(), "no age identity") {
		t.Fatalf("want ErrNoKey, got %v", err)
	}
	if len(s.Names()) != 3 {
		t.Fatal("names must not need the key")
	}
	if r := s.Check(); r.OpensHere == nil || *r.OpensHere {
		t.Fatalf("store present, no identity ⇒ opens_here false: %+v", r)
	}
	// without sops at all
	s.Lookup = func(string) (string, error) { return "", os.ErrNotExist }
	if _, err := s.Load(); err != ErrNoSops {
		t.Fatalf("want ErrNoSops, got %v", err)
	}
}

func TestLoadIsCachedPerStoreMtime(t *testing.T) {
	s := fixture(t, map[string]string{"YAHOO_USER": "u"}, "")
	calls := 0
	inner := s.Exec
	s.Exec = func(bin string, args []string, env []string) ([]byte, int, error) {
		if args[0] == "-d" {
			calls++
		}
		return inner(bin, args, env)
	}
	s.Load()
	s.Load()
	if calls != 1 {
		t.Fatalf("decrypts %d, want 1", calls)
	}
	if err := s.Set("BETA", "b", Meta{}); err != nil {
		t.Fatal(err)
	}
	s.Load()
	if calls != 2 {
		t.Fatalf("a Set must invalidate the cache: decrypts %d", calls)
	}
}

func TestInjectFollowsThePolicyAndRedacts(t *testing.T) {
	s := fixture(t, map[string]string{"YAHOO_USER": "u-long-value", "YAHOO_APP_PASSWORD": "u-long", "NVIDIA_API_KEY": "n"}, "")
	inj, err := s.Inject()
	if err != nil || len(inj.Env) != 0 {
		t.Fatalf("no policy ⇒ nothing: %+v %v", inj, err)
	}
	if err := os.WriteFile(s.PolicyPath(), []byte("YAHOO_*\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	inj, err = s.Inject()
	if err != nil || strings.Join(inj.Names, ",") != "YAHOO_USER,YAHOO_APP_PASSWORD" || strings.Join(inj.Env, ",") != "YAHOO_USER=u-long-value,YAHOO_APP_PASSWORD=u-long" {
		t.Fatalf("%+v %v", inj, err)
	}
	// longest first: the shorter secret is a prefix of the longer one
	got := string(Redact([]byte("a u-long-value b u-long c"), inj.Values))
	if got != "a [REDACTED:YAHOO_USER] b [REDACTED:YAHOO_APP_PASSWORD] c" {
		t.Fatalf("redact %q", got)
	}
}

func TestPresenceTellsWhatExistsAndWhatIsInjected(t *testing.T) {
	s := fixture(t, map[string]string{"YAHOO_USER": "u", "NVIDIA_API_KEY": "n"}, "YAHOO_*\n")
	meta := map[string]Meta{
		"NVIDIA_API_KEY": {Origin: OriginOperator, Created: "2026-09-06T10:00:00Z", Service: "nvidia"},
		"YAHOO_USER":     {Origin: OriginMaro, Updated: "2026-09-06T11:00:00Z", Run: "1a2b3c4d"},
	}
	b, _ := json.Marshal(meta)
	if err := os.WriteFile(s.MetaPath(), b, 0o600); err != nil {
		t.Fatal(err)
	}
	inj, _ := s.Inject()
	p := s.Presence(inj.Names, "", "/ws/drop/"+DropName)
	for _, want := range []string{
		"## Secrets",
		"Names in the store: YAHOO_USER (maro-derived by run 1a2b3c4d, 2026-09-06), NVIDIA_API_KEY (operator, 2026-09-06, nvidia).",
		"Injected into your environment as variables: YAHOO_USER.",
		"Not injected (you are on the host, same user as the operator): NVIDIA_API_KEY.",
		"sops -d --extract '[\"NAME\"]' " + s.StorePath(),
		"/ws/drop/" + DropName + " ($" + DropEnv + ")",
	} {
		if !strings.Contains(p, want) {
			t.Fatalf("presence lacks %q:\n%s", want, p)
		}
	}
	if strings.Contains(p, "=u") || strings.Contains(p, "=n") {
		t.Fatal("a value leaked into the presence block")
	}
	// with a hand-off file the block names the file, not variables
	pf := s.Presence(inj.Names, "/ws/drop/"+FileName, "")
	if !strings.Contains(pf, "Injected for this step as NAME=value lines in /ws/drop/"+FileName+" ($"+FileEnv+"; mode 0600, shredded when the step ends): YAHOO_USER.") || strings.Contains(pf, "environment as variables") {
		t.Fatalf("file wording:\n%s", pf)
	}
	if s.FrameSuffix(nil, "", "") != "\n\n"+s.Presence(nil, "", "") {
		t.Fatal("frame suffix shape")
	}
	if New(t.TempDir()).FrameSuffix(nil, "", "") != "" {
		t.Fatal("no store ⇒ empty suffix")
	}
}

func TestSetRecordsMetadataAfterTheValue(t *testing.T) {
	s := fixture(t, map[string]string{"MARO_SECRETS_STORE": "1"}, "")
	if err := s.Set("1BAD", "x", Meta{}); err == nil {
		t.Fatal("bad name accepted")
	}
	if err := s.Set("TOKEN", "t1", Meta{Service: "svc"}); err != nil {
		t.Fatal(err)
	}
	m := s.Meta()["TOKEN"]
	if m.Origin != OriginOperator || m.Service != "svc" || m.Created == "" || m.Created != m.Updated {
		t.Fatalf("%+v", m)
	}
	if err := s.Set("TOKEN", "t2", Meta{Origin: OriginMaro, Source: "drop", Run: "r9"}); err != nil {
		t.Fatal(err)
	}
	m2 := s.Meta()["TOKEN"]
	if m2.Origin != OriginMaro || m2.Run != "r9" || m2.Created != m.Created {
		t.Fatalf("%+v", m2)
	}
	if v, _, _ := s.Get("TOKEN"); v != "t2" {
		t.Fatalf("value %q", v)
	}
	if strings.Contains(Describe("TOKEN", s.Meta()), "t2") || !strings.HasPrefix(Describe("TOKEN", s.Meta()), "TOKEN (maro-derived by run r9, ") {
		t.Fatalf("describe %q", Describe("TOKEN", s.Meta()))
	}
	// no store: refused, names the init verb
	if err := New(t.TempDir()).Set("X", "1", Meta{}); err == nil || !strings.Contains(err.Error(), "secrets init") {
		t.Fatalf("want refusal, got %v", err)
	}
}

func TestIngestDropStoresAsMaroDerivedAndShreds(t *testing.T) {
	s := fixture(t, map[string]string{"MARO_SECRETS_STORE": "1"}, "")
	drop := filepath.Join(t.TempDir(), DropName)
	if err := os.WriteFile(drop, []byte("MINTED=\"abc def\"\nCOOKIE=c=1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	stored, err := s.IngestDrop(drop, "1a2b3c4d")
	if err != nil || strings.Join(stored, ",") != "MINTED,COOKIE" {
		t.Fatalf("%v %v", stored, err)
	}
	if _, err := os.Stat(drop); !os.IsNotExist(err) {
		t.Fatal("drop must be removed after a full ingest")
	}
	if v, _, _ := s.Get("MINTED"); v != "abc def" {
		t.Fatalf("value %q", v)
	}
	if m := s.Meta()["COOKIE"]; m.Origin != OriginMaro || m.Source != "drop" || m.Run != "1a2b3c4d" {
		t.Fatalf("%+v", m)
	}
	// a bad name keeps the file and stores the rest
	if err := os.WriteFile(drop, []byte("GOOD=1\n1BAD=2\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	stored, err = s.IngestDrop(drop, "r")
	if err == nil || strings.Join(stored, ",") != "GOOD" || !strings.Contains(err.Error(), "1 of 2 names stored") {
		t.Fatalf("%v %v", stored, err)
	}
	if _, err := os.Stat(drop); err != nil {
		t.Fatal("a partial ingest must keep the drop")
	}
	// missing / empty drops are no-ops
	if stored, err := s.IngestDrop(filepath.Join(t.TempDir(), "nope"), "r"); stored != nil || err != nil {
		t.Fatalf("%v %v", stored, err)
	}
	empty := filepath.Join(t.TempDir(), DropName)
	os.WriteFile(empty, []byte("# nothing\n"), 0o600)
	if stored, err := s.IngestDrop(empty, "r"); stored != nil || err != nil {
		t.Fatalf("%v %v", stored, err)
	}
	if _, err := os.Stat(empty); !os.IsNotExist(err) {
		t.Fatal("an empty drop is removed")
	}
	// no store: the drop is kept, the error names init
	bare := New(t.TempDir())
	keep := filepath.Join(t.TempDir(), DropName)
	os.WriteFile(keep, []byte("X=1\n"), 0o600)
	if _, err := bare.IngestDrop(keep, "r"); err == nil || !strings.Contains(err.Error(), "secrets init") {
		t.Fatalf("%v", err)
	}
	if _, err := os.Stat(keep); err != nil {
		t.Fatal("without a store the drop must survive")
	}
}

func TestOpenResolvesTheDir(t *testing.T) {
	t.Setenv(EnvDir, "/x/secrets")
	if Open().Dir != "/x/secrets" {
		t.Fatal("env override ignored")
	}
	t.Setenv(EnvDir, "")
	home, _ := os.UserHomeDir()
	if Open().Dir != filepath.Join(home, DefaultRel) {
		t.Fatalf("default %s", Open().Dir)
	}
}

func TestReportRenders(t *testing.T) {
	s := fixture(t, map[string]string{"YAHOO_USER": "u"}, "YAHOO_*\n")
	r := s.Check().Render()
	for _, want := range []string{"names (1): YAHOO_USER", "opens here:    yes", "inject policy: YAHOO_*", "injectable:    YAHOO_USER"} {
		if !strings.Contains(r, want) {
			t.Fatalf("render lacks %q:\n%s", want, r)
		}
	}
	if strings.Contains(r, "=u") {
		t.Fatal("value in report")
	}
}
