- evidence_refs: files or saved outputs you relied on. Every one must exist: a repository path, or a
  saved output's path from recent_stages or failure_history. A retry needs at least one.
- example: for a retry, the diagnosed cause as one concrete case in plain English: "Given <the attempt's
  exact output>, when <the runner checked it>, then <it rejected it because ...>".
- probe: for a retry, a shell command that exits 0 exactly when the files you cite show that cause. The
  runner copies ONLY the run files you cite into a scratch tree, each at run/<its file name> (repository
  files keep their own paths), and runs the probe there; it rejects your report if the probe does not
  exit 0 or needs a file you did not cite. For example: python3 -c "import json; r = json.load(open(
  'run/astra_discovery-03.json')); assert ' ' in r['code_refs'][0]". When no command can show the cause
  (a judgement about two positions), leave probe "" and say why in untestable; otherwise untestable is "".
