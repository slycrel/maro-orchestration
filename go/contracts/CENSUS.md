# Record census (GENERATED from record.Register calls)

| kind | envelope | schema | writer | authoritative reader | decision it affects | retention |
|---|---|---|---|---|---|---|
| `lease` | control | `lease/1` | workspace.Lease.Acquire | workspace.Lease (admission check on start) | refuse a second process on the same root; take over a stale lease with epoch+1 | bounded |
| `thought_stored` | production | `thought_stored/1` | thought.Store.Put | thought.Store.Get (hash re-verified on read); receipts and edges resolve refs through it | resolve a ThoughtRef to whole bytes or refuse (tamper/absent) — never a partial body | forever |
