#!/usr/bin/env bash
# Build the Go engine from this checkout and install it as the binary the
# Python shadow lane runs (`shadow.go.binary`, default ~/.local/bin/maro-go).
#
# The shadow lane pins every row to `go_binary_sha256`, so a landed Go
# change is NOT live until this runs — the same "landed but not
# materialized" trap the Python side documents for worktree lands.
#
# usage: scripts/install-maro-go.sh [dest]      (dest default: ~/.local/bin/maro-go)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dest="${1:-$HOME/.local/bin/maro-go}"
mkdir -p "$(dirname "$dest")"

# Build outside the shared GOPATH/GOCACHE when the caller has none: a
# scratch cache keeps a rebuild from contending with a running test suite.
export GOPATH="${GOPATH:-${TMPDIR:-/tmp}/maro-go-build/gopath}"
export GOCACHE="${GOCACHE:-${TMPDIR:-/tmp}/maro-go-build/gocache}"

commit="$(git -C "$here" rev-parse --short HEAD 2>/dev/null || echo unknown)"
dirty=""
if [ -n "$(git -C "$here" status --porcelain -- go 2>/dev/null)" ]; then
  dirty=" (go/ dirty)"
fi

tmp="$(mktemp "${dest}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
(cd "$here/go" && go build -buildvcs=false -o "$tmp" ./cmd/maro-go)
chmod 0755 "$tmp"
mv -f "$tmp" "$dest"   # atomic: a sweep mid-exec keeps its old inode
trap - EXIT

sha="$(sha256sum "$dest" | cut -d' ' -f1)"
echo "installed $dest"
echo "  commit  $commit$dirty"
echo "  sha256  $sha"
"$dest" workspace >/dev/null 2>&1 && echo "  smoke   ok (workspace resolves)" || echo "  smoke   FAILED: $dest workspace" >&2
