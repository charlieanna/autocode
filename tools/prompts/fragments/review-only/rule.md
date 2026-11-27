
REVIEW ONLY RUN. The person chose Review only: this run judges an existing change and changes nothing in
the project. Its one deliverable is {report}; no other file in the repository is created, changed or
deleted. The runner puts back any other file the Builder changes and sends the Builder back, and refuses
completion while one stays changed. Run tests and other code only in a scratch copy under
.autocode/scratch/, never in the project itself. Cite evidence by the file's path in the repository,
never by its path in the scratch copy.

Find the change under review from the request, and name it in change_under_review: a pull request (its
text is in linked_issues when the request names one; read its diff with git, for example by fetching the
pull request's head or branch, or with gh where it is signed in), a branch (git diff BASE...BRANCH), a
commit or commit range, a patch file, or the uncommitted change (git diff HEAD). When the request names
none, review the uncommitted change, or the current branch against the default branch when nothing is
uncommitted. Ask the person only when two or more candidates fit and the request does not say which.

Requirements leaves required_behaviors and acceptance_tests empty, as for any review, and does not ask
what format the findings take or whether to change files: both are fixed here. The plan is one milestone
whose only affected path is {report}. Its criteria: the file exists in the shape below, every finding names
a real file and lines of the change as it would be after merging, and every blocking finding is confirmed:
the Tester reproduces it from its evidence (a command run in a scratch copy, or the quoted lines) and the
review lists nothing as blocking that the Tester could not confirm. The runner itself keeps every other
file as it was, so plan no criterion or check for that, and never name git status in one.

The shape of {report}:
{{"verdict": "approve" or "request_changes",
 "change_under_review": "what was reviewed: the pull request, branch, range, patch or uncommitted change",
 "findings": [{{"id": "F1", "severity": "blocking" or "advisory", "file": "path in the repository",
   "lines": [first, last], "summary": "what is wrong", "evidence": "how you know: a command and its result,
   or the lines and the rule they break"}}],
 "tests_run": ["each command run, and where"]}}
A blocking finding must be fixed before merge: a behavior that regresses, a broken invariant, an
incompatible change, a security flaw, data loss. Everything else (style, naming, simplification) is
advisory. A missing test is advisory too: report the behavior it would have caught as its own blocking
finding instead. When nothing blocks the merge the verdict is approve; do not invent problems to look thorough.
