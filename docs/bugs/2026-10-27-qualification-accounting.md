# Qualification measurement scope and missing spend (#2217)

On master `3fee4fdad7f1e7cd2de210b076ea9004204d0f9e`, a qualifier run
record sums only accepted attempts for one role/model. Its model-selection
summary displayed an unqualified median, dropping unknown values without a
sample denominator. Failed and report-only work could therefore disappear from
the displayed economics even though those attempts consumed time/money.

A deterministic example has an accepted 60-second/$0.25 attempt, a rejected
600-second/$2 attempt and a 20-second unpriced report repair. The historical
fields correctly remain one accepted attempt, one minute and $0.25 for
compatibility, but they do not describe the cost of delivering the task.
The additive `attempt_accounting` record retains all three attempts, 680
recorded active seconds and $2.25 known reported spend with one unknown amount.
It never turns that subtotal into a complete total or adds a historical estimate
to a provider-reported amount. Existing public accounting deduplicates native
receipts before this projection; its attributed reported amounts are retained.

The summary now separates legacy accepted-only measurements from new all-attempt
role/model measurements, labels failed/repair scope and metric coverage, and
states that these are not whole-task totals or invoices. It does not pool the
two cohorts, backfill historical records or change qualification/ranking.
Failed-only, excluded and unreached routes remain unqualified. Role-active sums
are not wall time. Separate cost-per-accepted-task and transport/benchmark
qualification remain under #2059/#1867.

Four focused regressions fail on unchanged master: missing all-attempt records,
unlabeled legacy scope/coverage, mixed-cohort presentation and acceptance of an
infinite metric. The fix passes the public qualifier/selector/models-selection
consumers, including actual public accounting normalization of a shared native
receipt, unpriced/uncertain/active usage, pending workers, zero values, malformed
records and actual route-slot attribution. No provider process or paid model
request is needed to exercise this reporting path.
