# maro-go — the successor engine (branch `successor`)

Design: `../planning/successor-design-v1.md` (read the one-page vision read
first). Build log: `../planning/build-log.md`. Contracts: `contracts/`
(generated + declared pairs, `README.md` answer key, `CENSUS.md`).

```sh
export PATH=$HOME/.local/bin:$PATH
go test ./...
go run ./cmd/maro-go workspace           # resolves + announces the root, shows the lease
go run ./cmd/maro-go contracts check     # regeneration drift gate (the diff is the review)
go run ./cmd/maro-go contracts report    # three-state report; warnings are honest, errors fail
go run ./cmd/maro-go contracts gen       # regenerate after a type change; commit in the same change
go run ./cmd/maro-go judgment report     # shadow-arm agreement, disagreements, latency (prints; never written to the repo)
go run ./cmd/maro-go judgment replay --corpus ../tests/fixtures/validation_cases.json --providers llm,jev,hosted,pcd
```

Judgment providers and their defaults: [DEFAULTS.md](DEFAULTS.md). The
shadow arm (`--judge-shadow a,b`) is empty by default and can never change
a verdict — its records are control records, and the resolver reads
production only.

The successor's workspace root is its own (`~/.maro-go/workspace`, override
`MARO_GO_WORKSPACE`), never the Python engine's `~/.maro/workspace`.
