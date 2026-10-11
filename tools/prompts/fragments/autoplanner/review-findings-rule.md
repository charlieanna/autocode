
REVIEW FOLLOW-UP: review_findings in the handoff data are the findings of a review the user asked for earlier
in this conversation, saved in the repository at report_path. The user's request (task) now asks to act on
them, and they are the requirements: plan a fix for each blocking finding, with an acceptance criterion and a
regression test that fails on the reviewed change and passes after the fix. When the reviewed change is a patch
file (change_patch) that is not applied yet, the plan applies it first and fixes the findings on top of it, so
the change the user asked to land keeps everything else it does. Behavior the reviewed change already has and
must keep (what its own tests cover, what no finding says is broken) is a "guard:" criterion, never "test:": it
cannot fail on the reviewed change. The runner proves each regression test itself,
against the base code with change_patch applied (the change the review judged): do not add a criterion or task
to capture the tests failing without the fixes. Leave the advisory findings as they are
unless the request asks for them. Do not ask the user what the findings mean; ask only about a genuine choice
they leave open. Cite the review in code_refs as exactly its report_path.
