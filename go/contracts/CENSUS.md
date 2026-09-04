# Record census (GENERATED from record.Register calls)

| kind | envelope | schema | writer | authoritative reader | decision it affects | retention |
|---|---|---|---|---|---|---|
| `invocation` | production | `invocation/1` | invoke.Shell.Invoke (prepared, before dispatch) | invoke.State fold; Reconcile on restart; the receipts view | what was asked of which backend under which purpose/target; the effect-token namespace | forever |
| `invocation_dispatched` | production | `invocation_dispatched/1` | invoke.Shell.Invoke | invoke.Reconcile | an invocation without a terminal is presumed effectful | forever |
| `invocation_reconciled` | production | `invocation_reconciled/1` | invoke.Reconcile (on restart) | the run driver (retry vs escalate); the supervisor health line | retry an abandoned call or escalate an indeterminate one — never blind replay | forever |
| `lease` | control | `lease/1` | workspace.Lease.Acquire | workspace.Lease (admission check on start) | refuse a second process on the same root; take over a stale lease with epoch+1 | bounded |
| `receipt` | production | `receipt/1` | invoke.Shell | the run driver (response); metering; the receipts view; experiments (replay) | the response and its cost | forever |
| `terminal_observed` | production | `terminal_observed/1` | invoke.Shell (after response + transcript are stored) | invoke.State fold; Reconcile (finalizes a missing receipt from it) | how the stream ended; the receipt is derivable from it | forever |
| `thought_stored` | production | `thought_stored/1` | thought.Store.Put | thought.Store.Get (hash re-verified on read); receipts and edges resolve refs through it | resolve a ThoughtRef to whole bytes or refuse (tamper/absent) — never a partial body | forever |
| `tool_effect` | production | `tool_effect/1` | invoke.Shell (the moment the backend announces a tool_use) | judges (provenance/fabrication); Reconcile; the receipts view | what the backend set out to do; per-effect keys for reconciliation | forever |
| `tool_effect_result` | production | `tool_effect_result/1` | invoke.Shell (as the backend stream reports a tool_result) | judges; Reconcile (an observed effect without a result is outcome-unknown) | what the tool answered; is_error | forever |
