# Writing workflow

- Drafting and revising happen in draft.md, with me.
- To finalize, run exactly:
  REWRITE_MODEL=rewrite-pinned python3 rewrite.py draft.md -o final.md
  If my_writing.txt exists in this folder, add: --style my_writing.txt
  Do not finalize until I say "finalize".
- Never edit, rewrite, proofread or retype final.md, and never paste its
  contents back into a reply. You may read it and the run's stderr report.
- After a run, report the exit code meaning:
  0 = all units passed.
  3 = some units flagged: list the flagged unit numbers and reasons, then stop.
      I fix those by hand or change draft.md and rerun.
  1 = Ollama isn't reachable or the backend failed: tell me to start Ollama.
  2 = bad arguments: show me the error.
