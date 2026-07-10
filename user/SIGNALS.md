# Active Signals — operator template

<!--
  WHAT THIS FILE IS
  External signals and active research threads the system should be aware
  of when proposing or executing missions. Two readers:
    - src/planner.py injects the first ~500 chars into every
      goal-decomposition prompt
    - src/evolver_scans.py feeds the first ~600 chars to signal scanning,
      so proposed sub-missions get weighted toward your declared threads

  WHERE YOUR REAL FILE GOES
  Do NOT put personal research threads in this repo copy — it ships with
  the code. Put your real file at:  ~/.maro/workspace/user/SIGNALS.md
  (more precisely: <workspace_root>/user/SIGNALS.md). The workspace overlay
  always wins over this shipped template. See user/README.md.

  Updated by the evolver or manually as context changes.
-->

## Active research threads
<!-- Example:
     - Retrieval-augmented generation evaluation methods
     - CI flakiness patterns in the main repo -->

## Tools available
<!-- CLIs/services the system may assume exist on this box.
     Example:
     - `gh` CLI — GitHub operations
     - Jina Reader (`r.jina.ai`) — web content to markdown -->

## Constraints
<!-- Standing operational limits.
     Example:
     - No real-money actions without explicit approval
     - Plan heavy runs outside working hours -->
