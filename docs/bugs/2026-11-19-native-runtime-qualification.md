# Qualify installed runtimes without incidental path and launcher assumptions

The contained-shell consumer check exposed two failures already present on
master. Both reproduced with a clean, worktree-local Python environment: a
virtualenv in a linked directory was rejected before its native check, and the
installed pytest console launcher was refused before any model call.

The read-only Seatbelt renderer used the write-authority path validator for
additional read roots. That rejected a legitimate virtualenv alias even though
the shared read-root policy and Linux renderer already resolved such aliases.
It now uses the existing canonical read-root helper. Write authority still
refuses aliases, home/root grants remain refused, and a later link change cannot
broaden the path in an already rendered policy.

The pytest qualifier matched entire installer boilerplate bodies. A current
pip launcher uses a pure `argv[0].removesuffix` cleanup in place of the older
conditional suffix slice, so the valid launcher no longer matched. Recognition
now removes only proven-pure, nonexpanding program-name cleanup from the static
tree, then checks the same exact imports, main guard and pytest entry call.
It executes no launcher or project Python. Paired-interpreter and size checks
remain, and external calls, arbitrary regexes, expansion, argument mutation,
keyword calls and other extra code are still refused.

The original native pytest and linked-virtualenv tests fail on master and pass
with these changes. New regressions also fail against the original source.
Cases beyond the reported launcher include prefix and suffix removal chains,
conditional prefix slicing, whitespace cleanup, and adversarial inputs. The
26-test initial group passes, including actual native checks and file-denial
assertions. The affected selection ran 789 tests in 35 modules: 34 modules
passed; the sole visual-client fixture failure was reproduced on unchanged
master and corrected separately in PR #3928. After rebasing onto that verified
merge, all 24 visual-profile tests pass (one unrelated opt-in skip). Full Mypy
and Ruff lint/format pass. CI and merge evidence are maintained in the PR. These are runtime-admission fixes, with no model route, limit, new
backend or changed source-write permission.
