# Arena missed large integer subclasses

The Humanize case originally checked positive and negative built-in integers,
ordinary inputs and non-finite values. A retained live candidate passed those
four checks but used `type(value) is int` for its large-integer path. An ordinary
subclass of `int`, with the same positive or negative value beyond the floating
point range, still raised `OverflowError`.

The oracle now checks both signs with an ordinary integer subclass, and the
problem description explicitly includes it. The pinned upstream reference
passes all five checks. The pinned broken baseline fails the large built-in
integer checks and the subclass check. The incomplete historical candidate
fails only the new subclass check.

Existing attempts retain their original four-check scores. The revised case
must be prepared into a fresh evaluator directory and run in a new cohort.
The extra check establishes this input boundary; it does not replace Humanize's
upstream test suite or establish private holdout performance.
