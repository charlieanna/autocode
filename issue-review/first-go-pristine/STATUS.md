The first Go project scenario can discover the live candidate's ancestor module
when the new tests run in the nested original-source copy. The first-project
policy now takes its absence evidence from the separate pristine baseline and
requires passing named candidate tests. The regression fails on master because
it still launches the nested original-side suite.

This branch isolates the first-Go change from #3981. It retains the local
CLI proof and adds an offline Extension Host case. That case starts a real task
on the existing scenario, opens the expanded plan, binds the exact reviewed
approval, clicks Approve, and expects the verified completion card and the
runner's original-side absence evidence. Local VS Code closed before opening
its window. The Linux Extension Host job and required CI are outstanding.

This work does not resolve protected-link replay or the general proof
read boundary in #4057.
