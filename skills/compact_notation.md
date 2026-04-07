---
name: compact_notation
description: "Compressed notation vocabulary for agent reasoning — reduces token usage in intermediate steps without loss of precision"
roles_allowed: [worker, director, researcher]
triggers: [compact, shorthand, verbose, token-efficient]
always_inject: false
---

## Compact Notation Reference

When this skill is active, use the following shorthand in your reasoning, step results, and intermediate outputs. Full English is still required for user-facing summaries.

### Status / outcome
```
ok      = success / completed
err     = error / failed
blk     = blocked
skip    = skipped
?       = uncertain / unknown
→       = leads to / results in
✗       = explicitly wrong / rejected
```

### Action types
```
r:      = research / retrieve
bld:    = build / implement
exec:   = execute / run
cfg:    = configure / setup
tst:    = test / verify
fix:    = fix / repair
chk:    = check / inspect
```

### Common modifiers
```
w/      = with
w/o     = without
re:     = regarding
n=      = count is
~       = approximately
>N      = more than N
<N      = fewer than N
@       = at / location
#       = number of
```

### Orchestration-specific
```
sts:    = status
src:    = source / origin
res:    = result / response
cfg:    = config / configuration
dep:    = dependency
iter:   = iteration
ctx:    = context
tok:    = tokens
ms:     = milliseconds
```

### Usage examples

Instead of: "I successfully fetched the data from the API and parsed the JSON response."
Write: "r: API ok, res: JSON parsed"

Instead of: "There are 3 TODO items remaining and 1 item is currently blocked."
Write: "n=3 todo, n=1 blk"

Instead of: "The configuration file is missing the API key which is required."
Write: "cfg: missing API key (req'd)"

## Notes

- Use in `result` and `summary` fields of step outputs — NOT in user-facing explanations.
- Ambiguous abbreviations must be spelled out (e.g. if "r:" could mean "result" or "research" in context, use the full word).
- This vocabulary is additive — normal English is always valid.
