# A section label caused an unnecessary Requirements repair

A real OpenCode run on master e18cf660 used Codex Luna for Requirements,
Sol for planning and Astra for plan review. Luna's first report preserved all
17 task obligations and the exact examples, but the runner rejected it because
the standalone label `Required examples:` was neither quoted nor ignored.
One native repair added that label to `ignored_statements`; no behavior needed
correction. The run then completed all five planning stages and reached its
normal plan approval gate, with no Builder work.

An initial prompt clarification was tested with the same real Luna task. It
preserved all 17 obligations, but emitted an object containing `text` and
`summary` inside `ignored_statements`, which requires strings. One native repair
converted that object to a string. That candidate failed the target of accepting
the first report without repair.

The Requirements prompt now shows the required JSON string-array shape and
puts explanations only in the top-level `summary`. Actual examples, expected
results and behaviors remain mandatory. Operative constraints, corrections
and literal requested output cannot be ignored just because they resemble
headings. The coverage scanner and report schema are unchanged.

This addresses report-format guidance. A successful native check qualifies its
particular report; it cannot guarantee that every model will follow the prompt.
