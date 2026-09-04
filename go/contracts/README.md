# Contracts answer key (GENERATED — edit answerkey.go, not this file)

Every key a `.declared.json` may use: the meaning, a concrete wire example, and the evidence crumb. Someone saying "wha? huh?" follows the crumb and can disagree with evidence rather than a shrug.

Format version: `2026-09-04` (every pair carries `format_version`; a file without one reads as the founding revision). A key without `since` cannot be cited.

| key | value | meaning | wire example | evidence | since |
|---|---|---|---|---|---|
| `lifecycle` | `stable` | shape chosen AND a new consumer could adopt it as-is; generate and guard | `"lifecycle": "stable"` | contract-testing-input.md §5 | 2026-09-04 |
| `lifecycle` | `transitional` | meant to die; route + failure semantics only; deprecation lifecycle applies | `"lifecycle": "transitional"` | ibid. | 2026-09-04 |
| `lifecycle` | `internal-loose` | deliberately unformalized internal edge; warnings stand as accepted risk | `"lifecycle": "internal-loose"` | ibid. | 2026-09-04 |
| `lifecycle` | `hardened-legacy` | wrong-but-shipping; guard like stable PLUS design_flag (what is wrong, who owns it, sunset trigger); wins ties with stable | `"lifecycle": "hardened-legacy", "design_flag": "…"` | ibid. | 2026-09-04 |
| `lifecycle` | `design-pending` | shape unowned or contested; suppress test generation; escalation file beside the pair | `"lifecycle": "design-pending"` | ibid. | 2026-09-04 |
| `provenance` | `supplied` | stated by the owner (issue, design doc, the owner in the room) | `"provenance": "supplied"` | contract-testing-input.md §8 | 2026-09-04 |
| `provenance` | `inferred` | read off the implementation; evidence of what IS, not what was MEANT | `"provenance": "inferred"` | ibid. | 2026-09-04 |
| `absence` | `omitted` | the key is absent from the wire | `{"run_id" absent}` | generated: Omittable=true (omitempty/pointer/slice/map) | 2026-09-04 |
| `absence` | `null` | the key is present with a JSON null | `"supersedes": null` | measured per field | 2026-09-04 |
| `absence` | `empty` | the key is present with an empty value | `"attempt": 0` | measured per field | 2026-09-04 |
| `absence` | `never` | the writer always emits a value; absence cannot occur | `always present` | declared; executable as a writer pin | 2026-09-04 |
| `on_absence` | `tolerated` | a reader proceeds with the field unset | `—` | reference-reader backward test | 2026-09-04 |
| `on_absence` | `default:<v>` | a reader substitutes v | `"on_absence": "default:0"` | reference-reader backward test | 2026-09-04 |
| `on_absence` | `rejected` | a reader refuses the record | `—` | reference-reader backward test | 2026-09-04 |
| `unknown_value` | `accepted-unchanged` | an unknown vocabulary value is forwarded verbatim | `"envelope": "future" → kept` | reference-reader forward test | 2026-09-04 |
| `unknown_value` | `rejected` | an unknown value refuses the record | `—` | reference-reader forward test | 2026-09-04 |
| `unknown_value` | `replaced-with:<v>` | an unknown value is mapped to v | `"replaced-with:unset"` | reference-reader forward test | 2026-09-04 |
| `used_for` | `display-only | routing | authorization | money | storage-key | identity` | what decisions read the field; authorization + accepted-unchanged is ILLEGAL | `"used_for": "identity"` | report.go illegal combinations | 2026-09-04 |
| `constraint` | `defined` | a stated, executable constraint (pattern) | `"constraint": "defined", "pattern": "^s256v1:[0-9a-f]{64}$"` | report.go requires a pattern | 2026-09-04 |
| `constraint` | `unconstrained` | someone LOOKED and decided any value of the type is legal; executable (absurd value accepted). Thought fields: by decree (D16) | `"constraint": "unconstrained"` | thought_test.go feeds 8 MiB / empty / non-UTF-8 | 2026-09-04 |
| `constraint` | `undefined` | nobody looked — a WARNING, never silenced | `(absent)` | report.go | 2026-09-04 |
| `measured_by` | `<how> | not-re-runnable-here` | how a measured claim was established; not-re-runnable-here is the honest answer for a fact CI cannot re-establish, never a licence to omit | `"measured_by": "thought_test.go TestUnconstrainedBodies"` | contract-testing-input.md §16 | 2026-09-04 |
| `envelope` | `production | control | experimental` | the population; from the registry, never the value's marker | `"envelope": "production"` | record/registry.go; record_test.go TestRegistryIsTheAuthority | 2026-09-04 |
| `schema` | `<kind>/<n>` | contract version of the kind (D3); readers accept 1..n per declared absence semantics | `"schema": "outcome/2"` | record.SchemaVer.Parse | 2026-09-04 |
| `retention` | `forever | bounded | audit-only` | what happens to rows over time; audit-only must name its read surface | `—` | record.Spec census | 2026-09-04 |
| `format_version` | `YYYY-MM-DD` | the answer-key revision a file was emitted against | `"format_version": "2026-09-04"` | contract input §18 | 2026-09-04 |
| `improvised` | `{<x-key>: {used_at, search}}` | register of improvised keys in a declared file; a bare unknown key is a vocabulary warning, an x- key without a row is an error | `"improvised": {"x-status-variants": {"used_at": "field:state", "search": "grep -r status-variants contracts/"}}` | contract input §20; report.go | 2026-09-04 |

## Three states, one severity model

| state | written as | surfaces as |
|---|---|---|
| derived | the generated file | asserted automatically |
| declared | a line in `.declared.json` | asserted; fails if the system disagrees |
| undefined | absent from both | **warning** in the report |

Errors, exactly three ways: two derived facts contradict; a declared line fails its test; an illegal combination.
