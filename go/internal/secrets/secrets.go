// Package secrets is the Go engine's view of Maro's secrets store — the
// same store the Python engine manages (docs/SECRETS_DESIGN.md, decree
// 2026-09-06: secrets management is the fix for container blindness, in
// both engines). The store is a sops + age dotenv file whose NAMES are
// cleartext and whose VALUES are encrypted; this package reads names
// without the key, decrypts through the `sops` binary when the box holds
// the identity, applies the operator's inject policy, renders the
// presence index a worker is told, and folds a run's derived-secret drop
// back in. It never prints a value except through Get.
//
// Layout (machine-level, engine-neutral; MARO_SECRETS_DIR overrides):
//
//	~/.maro/secrets/maro.sops.env      the store
//	~/.maro/secrets/age-identity.txt   this box's age private key
//	~/.maro/secrets/inject             one name or glob per line
//	~/.maro/secrets/meta.json          cleartext metadata per name
//
// Management (init, migrate, set, recipients) is the Python CLI's; the Go
// engine reads, injects, tells, and ingests. Zero dependencies: the sops
// Go module would pull in every cloud KMS SDK for a contract that is one
// binary and two exit codes.
package secrets

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const (
	EnvDir       = "MARO_SECRETS_DIR"
	DefaultRel   = ".maro/secrets"
	StoreName    = "maro.sops.env"
	IdentityName = "age-identity.txt"
	PolicyName   = "inject"
	MetaName     = "meta.json"
	// DropName is the derived-secret hand-off file a worker writes; DropEnv
	// names it in the worker's environment.
	DropName = "secrets-derived.env"
	DropEnv  = "MARO_SECRETS_DROP"
	// FileName is the per-step hand-off file the subprocess backend writes
	// (0600, NAME=value lines) and shreds after the call; FileEnv names its
	// path to the child. On the host a process env is inherited by every
	// descendant and readable from /proc by the same user; a file the
	// child is merely told about is read on purpose (design §10, Jeremy
	// 2026-09-06). Same names as Python's secrets_store.FILE_NAME/FILE_ENV.
	FileName = "secrets.env"
	FileEnv  = "MARO_SECRETS_FILE"

	OriginOperator = "operator"
	OriginMaro     = "maro"

	exitNoKey = 128 // sops: no master key could decrypt the data key
)

// Meta is one name's cleartext record.
type Meta struct {
	Origin  string `json:"origin"`
	Created string `json:"created,omitempty"`
	Updated string `json:"updated,omitempty"`
	Run     string `json:"run,omitempty"`
	Service string `json:"service,omitempty"`
	Source  string `json:"source,omitempty"`
	Note    string `json:"note,omitempty"`
}

// Store is a resolved secrets dir plus the two process seams tests replace.
type Store struct {
	Dir    string
	Lookup func(string) (string, error) // exec.LookPath
	// Exec runs sops with args under env and returns stdout, exit code.
	Exec func(bin string, args []string, env []string) ([]byte, int, error)

	cacheStamp string
	cache      map[string]string
}

// Open resolves the dir: MARO_SECRETS_DIR, else $HOME/.maro/secrets. It
// never creates anything.
func Open() *Store {
	dir := strings.TrimSpace(os.Getenv(EnvDir))
	if dir == "" {
		if home, err := os.UserHomeDir(); err == nil {
			dir = filepath.Join(home, DefaultRel)
		}
	}
	return New(dir)
}

// New is a store over an explicit dir.
func New(dir string) *Store {
	return &Store{Dir: dir, Lookup: exec.LookPath, Exec: runExec}
}

func runExec(bin string, args []string, env []string) ([]byte, int, error) {
	cmd := exec.Command(bin, args...)
	cmd.Env = env
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()
	code := 0
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		code = ee.ExitCode()
		err = fmt.Errorf("sops exit %d: %s", code, strings.TrimSpace(tail(errb.String(), 300)))
	}
	return out.Bytes(), code, err
}

func tail(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[len(s)-n:]
}

func (s *Store) StorePath() string    { return filepath.Join(s.Dir, StoreName) }
func (s *Store) IdentityPath() string { return filepath.Join(s.Dir, IdentityName) }
func (s *Store) PolicyPath() string   { return filepath.Join(s.Dir, PolicyName) }
func (s *Store) MetaPath() string     { return filepath.Join(s.Dir, MetaName) }

// Present reports whether the store file exists.
func (s *Store) Present() bool {
	st, err := os.Stat(s.StorePath())
	return err == nil && st.Mode().IsRegular()
}

// ParseNames is the cleartext side of a sops dotenv file: names in file
// order, comments / blanks / sops_* metadata skipped, duplicates dropped.
// Pinned to the same reading as Python's secrets_store.parse_names.
func ParseNames(text string) []string {
	var out []string
	seen := map[string]bool{}
	for _, raw := range strings.Split(text, "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		name, _, found := strings.Cut(line, "=")
		name = strings.TrimSpace(name)
		if !found || name == "" || strings.HasPrefix(name, "sops_") || seen[name] {
			continue
		}
		seen[name] = true
		out = append(out, name)
	}
	return out
}

// Names reads the store without the key. Nil when there is no store.
func (s *Store) Names() []string {
	b, err := os.ReadFile(s.StorePath())
	if err != nil {
		return nil
	}
	return ParseNames(string(b))
}

// Recipients are the age public keys the store is wrapped to.
func (s *Store) Recipients() []string {
	b, err := os.ReadFile(s.StorePath())
	if err != nil {
		return nil
	}
	var out []string
	for _, line := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(line, "sops_age__list_") && strings.Contains(line, "__map_recipient=") {
			out = append(out, strings.TrimSpace(line[strings.Index(line, "=")+1:]))
		}
	}
	return out
}

// ParsePolicy: one glob per line, `#` comments.
func ParsePolicy(text string) []string {
	var out []string
	seen := map[string]bool{}
	for _, raw := range strings.Split(text, "\n") {
		line := raw
		if i := strings.Index(line, "#"); i >= 0 {
			line = line[:i]
		}
		line = strings.TrimSpace(line)
		if line == "" || seen[line] {
			continue
		}
		seen[line] = true
		out = append(out, line)
	}
	return out
}

// Policy reads the inject file; nil when absent.
func (s *Store) Policy() []string {
	b, err := os.ReadFile(s.PolicyPath())
	if err != nil {
		return nil
	}
	return ParsePolicy(string(b))
}

// Injectable keeps the names (store order) some glob matches. path.Match
// is case-sensitive and `*` does not cross `/`, which names never hold —
// the same set Python's fnmatchcase yields for ENV-style names.
func Injectable(names, globs []string) []string {
	if len(globs) == 0 {
		return nil
	}
	var out []string
	for _, n := range names {
		for _, g := range globs {
			if ok, _ := path.Match(g, n); ok {
				out = append(out, n)
				break
			}
		}
	}
	return out
}

func (s *Store) sopsEnv() []string {
	env := os.Environ()
	filtered := env[:0]
	for _, kv := range env {
		if !strings.HasPrefix(kv, "SOPS_AGE_KEY_FILE=") {
			filtered = append(filtered, kv)
		}
	}
	return append(filtered, "SOPS_AGE_KEY_FILE="+s.IdentityPath())
}

// ErrNoKey: the store exists but this box's identity cannot open it.
var ErrNoKey = errors.New("secrets: no age identity can open the store")

// ErrNoSops: the sops binary is not installed.
var ErrNoSops = errors.New("secrets: sops is not installed (brew install sops age)")

// Load decrypts the store: name → value. An absent store is (nil, nil).
// Cached per store mtime so a run's lookups cost one decrypt.
func (s *Store) Load() (map[string]string, error) {
	st, err := os.Stat(s.StorePath())
	if err != nil {
		return nil, nil
	}
	stamp := fmt.Sprintf("%d:%s", st.ModTime().UnixNano(), s.IdentityPath())
	if s.cache != nil && s.cacheStamp == stamp {
		return copyMap(s.cache), nil
	}
	bin, err := s.Lookup("sops")
	if err != nil {
		return nil, ErrNoSops
	}
	out, code, err := s.Exec(bin, []string{"-d", "--output-type", "json", s.StorePath()}, s.sopsEnv())
	if err != nil {
		if code == exitNoKey {
			return nil, fmt.Errorf("%w (SOPS_AGE_KEY_FILE=%s)", ErrNoKey, s.IdentityPath())
		}
		return nil, err
	}
	var raw map[string]any
	if err := json.Unmarshal(out, &raw); err != nil {
		return nil, fmt.Errorf("secrets: store decrypted to non-JSON: %v", err)
	}
	values := map[string]string{}
	for k, v := range raw {
		if strings.HasPrefix(k, "sops") {
			continue
		}
		switch t := v.(type) {
		case nil:
			values[k] = ""
		case string:
			values[k] = t
		default:
			values[k] = fmt.Sprint(t)
		}
	}
	s.cache, s.cacheStamp = copyMap(values), stamp
	return values, nil
}

func copyMap(m map[string]string) map[string]string {
	out := make(map[string]string, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

// Get is the one reader that hands out a value.
func (s *Store) Get(name string) (string, bool, error) {
	values, err := s.Load()
	if err != nil {
		return "", false, err
	}
	v, ok := values[name]
	return v, ok, nil
}

// Injection is what a tool-bearing subprocess receives.
type Injection struct {
	Names  []string          // injected names, store order
	Env    []string          // "NAME=value" for each
	Values map[string]string // name → value, for redaction
}

// Inject resolves the policy against the store. No policy, no store, or
// nothing decryptable ⇒ an empty injection and, when the store exists but
// will not open, the error (the caller warns; the frame still tells).
func (s *Store) Inject() (Injection, error) {
	names := Injectable(s.Names(), s.Policy())
	inj := Injection{Values: map[string]string{}}
	if len(names) == 0 {
		return inj, nil
	}
	values, err := s.Load()
	if err != nil {
		return inj, err
	}
	for _, n := range names {
		v, ok := values[n]
		if !ok {
			continue
		}
		inj.Names = append(inj.Names, n)
		inj.Env = append(inj.Env, n+"="+v)
		inj.Values[n] = v
	}
	return inj, nil
}

// Redact replaces every injected VALUE in text with [REDACTED:<NAME>],
// longest value first so an overlapping shorter secret cannot leave the
// tail of a longer one behind (the Python scrubber's rule).
func Redact(text []byte, values map[string]string) []byte {
	type kv struct{ k, v string }
	var items []kv
	for k, v := range values {
		if v != "" {
			items = append(items, kv{k, v})
		}
	}
	sort.Slice(items, func(i, j int) bool {
		if len(items[i].v) != len(items[j].v) {
			return len(items[i].v) > len(items[j].v)
		}
		return items[i].k < items[j].k
	})
	for _, it := range items {
		text = bytes.ReplaceAll(text, []byte(it.v), []byte("[REDACTED:"+it.k+"]"))
	}
	return text
}

// Meta reads meta.json; nil when absent or torn (the store keeps serving).
func (s *Store) Meta() map[string]Meta {
	b, err := os.ReadFile(s.MetaPath())
	if err != nil {
		return nil
	}
	var m map[string]Meta
	if err := json.Unmarshal(b, &m); err != nil {
		return nil
	}
	return m
}

func (s *Store) writeMeta(m map[string]Meta) error {
	b, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.MetaPath() + ".tmp"
	if err := os.WriteFile(tmp, append(b, '\n'), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, s.MetaPath())
}

// Describe renders `NAME (operator, 2026-09-06, yahoo)` /
// `NAME (maro-derived by run 1a2b, 2026-09-06)` — the presence index form.
func Describe(name string, meta map[string]Meta) string {
	m, ok := meta[name]
	if !ok {
		return name
	}
	var bits []string
	switch m.Origin {
	case OriginMaro:
		if m.Run != "" {
			bits = append(bits, "maro-derived by run "+m.Run)
		} else {
			bits = append(bits, "maro-derived")
		}
	case "":
	default:
		bits = append(bits, m.Origin)
	}
	stamp := m.Updated
	if stamp == "" {
		stamp = m.Created
	}
	if len(stamp) >= 10 {
		bits = append(bits, stamp[:10])
	}
	if m.Service != "" {
		bits = append(bits, m.Service)
	}
	if len(bits) == 0 {
		return name
	}
	return name + " (" + strings.Join(bits, ", ") + ")"
}

// Presence is the `## Secrets` paragraph for an execute frame — the same
// wording as Python's secrets_store.presence_block, host form (the Go
// engine runs its steps on the host as the operator's user). Empty
// without a store. drop, when set, is the path a worker drops a derived
// credential at.
// file, when set, is the hand-off path the injected values travel in
// (the env wording is used when it is empty — the no-scratch fallback).
func (s *Store) Presence(injected []string, file, drop string) string {
	known := s.Names()
	if len(known) == 0 {
		return ""
	}
	meta := s.Meta()
	inj := map[string]bool{}
	for _, n := range injected {
		inj[n] = true
	}
	var in, held, described []string
	for _, n := range known {
		described = append(described, Describe(n, meta))
		if inj[n] {
			in = append(in, n)
		} else {
			held = append(held, n)
		}
	}
	lines := []string{"## Secrets",
		"Credentials for this machine are managed by Maro's secrets store (sops + age; names are readable, values are encrypted). Names in the store: " + strings.Join(described, ", ") + "."}
	if len(in) > 0 && file != "" {
		lines = append(lines, FileInstructions(file, in))
	} else if len(in) > 0 {
		lines = append(lines, "Injected into your environment as variables: "+strings.Join(in, ", ")+".")
	}
	if len(held) > 0 {
		lines = append(lines, "Not injected (you are on the host, same user as the operator): "+strings.Join(held, ", ")+
			". Read one with `sops -d --extract '[\"NAME\"]' "+s.StorePath()+"` (SOPS_AGE_KEY_FILE="+s.IdentityPath()+"); never print or persist a value.")
	}
	if drop != "" {
		lines = append(lines, DropInstructions(drop))
	}
	return strings.Join(lines, "\n")
}

// FileInstructions tells a worker where this step's injected values are —
// identical to the Python wording (secrets_store.file_instructions).
func FileInstructions(file string, names []string) string {
	return "Injected for this step as NAME=value lines in " + file + " ($" + FileEnv + "; mode 0600, shredded when the step ends): " +
		strings.Join(names, ", ") + ". Read the line you need with `grep '^NAME=' $" + FileEnv + "` or source the file in a subshell; never print or persist a value."
}

// DropInstructions tells a worker how to hand back a credential it
// obtained — identical to the Python wording.
func DropInstructions(drop string) string {
	return "If you OBTAIN a new credential while working (an app password you minted, a token you were issued, a session cookie), do not put it in your result: append `NAME=value` lines to " +
		drop + " ($" + DropEnv + "). Maro stores it in the secrets store, records that this run derived it, and shreds the file. Say in your result WHICH name you dropped and for what service — never the value."
}

// FrameSuffix is what the engine appends to its execute frame: "" when
// there is nothing to say, else a blank line and the presence block.
func (s *Store) FrameSuffix(injected []string, file, drop string) string {
	p := s.Presence(injected, file, drop)
	if p == "" {
		return ""
	}
	return "\n\n" + p
}

// ParseDotenv: NAME=value pairs, comments skipped, one layer of matching
// quotes stripped — the drop file's format.
func ParseDotenv(text string) [][2]string {
	var out [][2]string
	for _, raw := range strings.Split(text, "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		k, v, found := strings.Cut(line, "=")
		k = strings.TrimSpace(k)
		if !found || k == "" {
			continue
		}
		v = strings.TrimSpace(v)
		if len(v) >= 2 && (v[0] == '"' && v[len(v)-1] == '"' || v[0] == '\'' && v[len(v)-1] == '\'') {
			v = v[1 : len(v)-1]
		}
		out = append(out, [2]string{k, v})
	}
	return out
}

// ValidName: ENV-style identifiers only.
func ValidName(name string) bool {
	if name == "" || name[0] >= '0' && name[0] <= '9' {
		return false
	}
	for _, c := range name {
		if !(c == '_' || c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z' || c >= '0' && c <= '9') {
			return false
		}
	}
	return true
}

// Set writes one value in place (`sops --set`) and records its metadata
// AFTER the value lands (a torn write leaves a value without a record,
// never a record without a value). Requires an existing store: creating
// one (identity + first encrypt) is the Python CLI's `secrets init`.
func (s *Store) Set(name, value string, m Meta) error {
	if !ValidName(name) {
		return fmt.Errorf("secrets: names are ENV-style identifiers, got %q", name)
	}
	if !s.Present() {
		return fmt.Errorf("secrets: no store at %s (run `maro secrets init`)", s.StorePath())
	}
	bin, err := s.Lookup("sops")
	if err != nil {
		return ErrNoSops
	}
	jv, _ := json.Marshal(value)
	if _, code, err := s.Exec(bin, []string{"--set", fmt.Sprintf("[%q] %s", name, jv), s.StorePath()}, s.sopsEnv()); err != nil {
		if code == exitNoKey {
			return ErrNoKey
		}
		return err
	}
	s.cache = nil
	all := s.Meta()
	if all == nil {
		all = map[string]Meta{}
	}
	now := time.Now().UTC().Format("2006-01-02T15:04:05Z")
	prev := all[name]
	if m.Created == "" {
		m.Created = prev.Created
		if m.Created == "" {
			m.Created = now
		}
	}
	m.Updated = now
	if m.Origin == "" {
		m.Origin = OriginOperator
	}
	all[name] = m
	return s.writeMeta(all)
}

// IngestDrop folds a worker's drop file in as maro-derived (source
// "drop", the run handle when known), then zero-fills and removes it. A
// name that fails, or a store that will not open, leaves the file in
// place — a derived credential is never lost to a torn hand-off. Returns
// the names stored.
func (s *Store) IngestDrop(dropPath, run string) ([]string, error) {
	b, err := os.ReadFile(dropPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	pairs := ParseDotenv(string(b))
	if len(pairs) == 0 {
		return nil, shred(dropPath, len(b))
	}
	if !s.Present() {
		return nil, fmt.Errorf("secrets: drop kept at %s: no store (run `maro secrets init`)", dropPath)
	}
	var stored []string
	var firstErr error
	for _, kv := range pairs {
		if err := s.Set(kv[0], kv[1], Meta{Origin: OriginMaro, Source: "drop", Run: run}); err != nil {
			if firstErr == nil {
				firstErr = err
			}
			continue
		}
		stored = append(stored, kv[0])
	}
	if len(stored) == len(pairs) {
		return stored, shred(dropPath, len(b))
	}
	return stored, fmt.Errorf("secrets: drop kept at %s: %d of %d names stored: %v", dropPath, len(stored), len(pairs), firstErr)
}

func shred(p string, size int) error {
	f, err := os.OpenFile(p, os.O_WRONLY, 0)
	if err == nil {
		_, _ = f.Write(make([]byte, size))
		_ = f.Sync()
		_ = f.Close()
	}
	return os.Remove(p)
}

// Report is `maro-go secrets check`.
type Report struct {
	Dir        string   `json:"dir"`
	Sops       string   `json:"sops,omitempty"`
	Store      string   `json:"store,omitempty"`
	Identity   string   `json:"identity,omitempty"`
	Recipients []string `json:"recipients"`
	Names      []string `json:"names"`
	OpensHere  *bool    `json:"opens_here"`
	Policy     []string `json:"policy"`
	Injectable []string `json:"injectable"`
	Error      string   `json:"error,omitempty"`
}

// Check is one status for the CLI and the frame's honesty: what is here,
// whether it opens, what the policy injects.
func (s *Store) Check() Report {
	r := Report{Dir: s.Dir, Recipients: s.Recipients(), Names: s.Names(), Policy: s.Policy()}
	if r.Recipients == nil {
		r.Recipients = []string{}
	}
	if r.Names == nil {
		r.Names = []string{}
	}
	if r.Policy == nil {
		r.Policy = []string{}
	}
	r.Injectable = Injectable(r.Names, r.Policy)
	if r.Injectable == nil {
		r.Injectable = []string{}
	}
	if bin, err := s.Lookup("sops"); err == nil {
		r.Sops = bin
	}
	if s.Present() {
		r.Store = s.StorePath()
	}
	if st, err := os.Stat(s.IdentityPath()); err == nil && st.Mode().IsRegular() {
		r.Identity = s.IdentityPath()
	}
	if r.Store != "" {
		opens := false
		if r.Sops != "" && r.Identity != "" {
			values, err := s.Load()
			if err != nil {
				r.Error = err.Error()
			} else {
				opens = len(values) > 0 || len(r.Names) == 0
			}
		}
		r.OpensHere = &opens
	}
	return r
}

// Render is the check report as text.
func (r Report) Render() string {
	or := func(v, alt string) string {
		if v == "" {
			return alt
		}
		return v
	}
	opens := "-"
	if r.OpensHere != nil {
		opens = map[bool]string{true: "yes", false: "no"}[*r.OpensHere]
	}
	lines := []string{
		"secrets dir:   " + r.Dir,
		"sops:          " + or(r.Sops, "MISSING (brew install sops age)"),
		"identity:      " + or(r.Identity, "none (maro secrets init)"),
		"store:         " + or(r.Store, "none (maro secrets init)"),
		fmt.Sprintf("recipients:    %d", len(r.Recipients)),
		fmt.Sprintf("names (%d): %s", len(r.Names), or(strings.Join(r.Names, ", "), "-")),
		"opens here:    " + opens,
		"inject policy: " + or(strings.Join(r.Policy, ", "), "(nothing injected)"),
		"injectable:    " + or(strings.Join(r.Injectable, ", "), "-"),
	}
	if r.Error != "" {
		lines = append(lines, "error:         "+r.Error)
	}
	return strings.Join(lines, "\n")
}
