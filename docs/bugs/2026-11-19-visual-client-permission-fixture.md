# Keep the offline native client's default before explicit permission rules

The native runtime qualification selection exposed a visual-profile fixture
failure already present on unchanged master. The owned offline client omitted
OpenCode's default shell allow rule whenever an explicit Bash rule table was
present. A table containing publication denials therefore had no default in
its original projection. After core intersected it with the strict tool
surface, the projection contained the restrictive wildcard, so the exact
effective-policy gate correctly refused the mismatch.

The fixture now emits the native default first, followed by explicit rules in
their supplied order. Explicit deny, ask, wildcard and more-specific rules
still take precedence. No production permission or conformance gate changes.
The client is factored into one fixture helper for its launch and projection
tests. A repository search found this conditional-default construction only
in this one owned client; the cause is narrow to its modeled native response.

The original real-production-worker test fails on unchanged master and passes
with the fixture correction. A new model-free regression checks defaults,
specific denials, explicit wildcard denial, specific allows and ask rules; it
fails with the old projection restored. All 24 visual-profile tests pass on
macOS, with one unrelated opt-in test skipped. The corrected test exercises the
actual adapter, worker builder and Seatbelt boundary, not native SDK or paid
model/image acceptance. CI and merge evidence are maintained in the PR.
