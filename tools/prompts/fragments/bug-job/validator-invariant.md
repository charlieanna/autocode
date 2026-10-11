
BUG INVARIANT: this change fixes a reproduced bug. Its diagnosis states the rule a correct fix must uphold:
{invariant}
The Builder's tests check a few examples of it. Check the rule itself, exactly as stated: capture your own check
that exercises it on inputs the tests do not use (for example many generated inputs, larger or multi-part ones)
and compares exactly, with no tolerance the rule does not allow. A violation is a FAIL, with the input that shows
it as evidence, even when every test passes.
