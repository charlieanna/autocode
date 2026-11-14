# An acceptance test recursively ran its own regression suite

The old TODO diagnostic (Core `4342ccc44c92029ca44bfbfc6ed471a823bc24a3`, bundle
manifest `674d78c83d0722000f14fbe0c1207e04ee3bf121b7babc5af8884c76a425c8fc`)
planned AC8 as a named test of suite delivery. The protected acceptance author
wrote `test_ac8_regression_suite_runs`, spawning `python -m unittest test_todo`
inside that module. Its reentry guard bounded nesting but still added the whole
suite to every outer invocation. Builders retained the exact function and later
added tests, which also entered the inner suite.

The acceptance diff hashes to
`dcbf549a89e3ce6de8b459a390d280087a13ebe62812a4a904596dbb46e606f2`;
the retained fixture's original source hashes to
`e521d929673fe642e7e2f07c38cfeedcf726b9929881e8d50c6f210f45017946`.
Proof03 (`b15c4e15952ee5e96a6ee616af45c142089b6d73864a6f778410f5cd5274c6b4`)
selected 17 tests per mutant and exhausted its 900-second budget with 34 of 79
mutants not run. Recursion is an avoidable cost multiplier; this evidence does
not establish that it alone caused exhaustion or quantify a candidate speedup.

The source check now refuses demonstrable own-module/file/discovery commands
before acceptance execution/protection and through the existing named-proof
wrapper policy. It reads AST and known literal bindings in collected test contexts;
later refusals apply only to the selected wrapper, preserving other behavioral
tests in the same file. Uncalled helpers, decorators/async/custom collection,
conditional/lazy definitions or bodies, replacing bindings, custom constructors/bases,
and shell/executable overrides remain unknown. Likewise,
indirect callgraphs, dynamic commands,
unknown working directories and unsupported selectors are not comprehensively
recognized; the shared authoring guidance forbids these self-suite wrappers too.
Distinct fixture suites, product CLI subprocesses and normal `unittest.main()`
entry points remain legitimate.

Acceptance preflight is an internal option enabled only for acceptance execution
and its exact recovery. It checks every resolved command before the first suite
runs, using that command's actual collector, selectors and package directory.
For scheduled preparation, a refusal has no inner product command: it is accepted
only as negative evidence bound to the exact request, source bytes, command,
immutable worker and collected closed outer receipt. It cannot grant test,
approval or completion authority. Other scratch consumers keep their defaults.
The worker atomically publishes its result and emits its byte hash into that
outer command's pinned capture. Missing, duplicate or changed publication
markers, changed captures/results, substituted derived selectors and root-like
text cannot authenticate a refusal. Rebasing applies only to explicit root/path
argument identities; it never rewrites JSON text or arbitrary substrings.

Future aggregate suite-delivery criteria use the actual current Core command,
named outcomes and proof artifact through the existing criterion evidence
contract. Their behavioral criteria retain named fail-first tests. Existing
approved criterion text, literals and test identities remain protected: the
frozen AC8 was not remapped or resumed. Completion gates, limits, schemas and
clients are unchanged. Offline regression evidence does not establish improved
native-model efficiency, editor qualification or completion of that old run.
