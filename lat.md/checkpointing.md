# Checkpointing

Write per-step progress to disk so loops can resume mid-run rather than restarting from scratch (GAP 3).

## Files

Source files that implement checkpoint read/write operations and the runtime checkpoint directory.

- `src/checkpoint.py` — `write_checkpoint()`, `load_checkpoint()`, `resume_from()`, `delete_checkpoint()`, `list_checkpoints()`
- `checkpoints/ckpt_{loop_id}.json` — runtime checkpoint files (not committed)

## Behavior

How checkpoints are written, retained, and consumed during loop execution.

- Written after every completed step in `src/agent_loop.py`
- Deleted on `status=done` (successful completion)
- Retained on `status=stuck` or `status=partial` for future resume
- Resume via `run_agent_loop(resume_from_loop_id="abc12345")`

## CLI

Commands for inspecting and managing saved checkpoints from the terminal.

```bash
poe-checkpoint list          # list all saved checkpoints
poe-checkpoint show <id>     # dump checkpoint JSON
poe-checkpoint delete <id>   # delete manually
```

## Related Concepts

Other systems that interact with or are distinguished from checkpointing.

- [[core-loop]] — checkpoints written inside `run_agent_loop()`
- [[memory-system]] — separate concern; memory is per-loop outcomes, checkpoints are intra-loop step state
