For an explicitly authorized baseline exception with Vitest default-reporter logs,
use baseline_compare_command with BASELINE_LOG CANDIDATE_LOG --output REPORT.json.
Use --baseline-root and --candidate-root only for equivalent checkout paths.
Do not invent a task-local comparator or loosen its checks. Unknown formats require review.
A matched comparison does not authorize a waiver: verify identical test selection,
source provenance, and the saved exception separately; investigate baseline-only failures.
