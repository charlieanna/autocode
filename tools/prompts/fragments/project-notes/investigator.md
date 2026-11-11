- project_notes: optional, at most three facts about this repository that the next task would otherwise
  pay to discover again, each as kind (reproduce_command, test_command, flaky_test, build_quirk,
  ownership or other) and text (one plain sentence or command, no local absolute paths, no secrets).
  Name only facts your evidence shows, not this run's task or diagnosis. They are kept only when your
  probe shows the diagnosed cause; otherwise use [].
  project_notes in the handoff, when present, are facts earlier runs learned here: advisory, so check
  one against the repository before you rely on it.
