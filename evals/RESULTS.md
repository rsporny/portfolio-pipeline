# Eval results

_Not yet generated in this checkout._

This scorecard is produced by `uv run pipeline eval` (and, in CI, by
`.github/workflows/evals.yml`), which runs the real transformer over the golden
cases in `evals/cases/` and scores each with the structural check library. The
run needs `ANTHROPIC_API_KEY`, so it is not part of unit CI; the file is committed
and overwritten on the next eval run.

Each run records the model, the commit, a check × case pass/fail matrix, a
failures list, and totals (error-severity failures / warnings). A `✗`
(error-severity) fails the job — the pre-merge gate for a prompt or model change.
