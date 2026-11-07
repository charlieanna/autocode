# Stable Python identities in nested joint proof

A live joint workspace inherited an ancestor's pytest root directory. JUnit
classnames consequently included different baseline/candidate scratch paths.
The runner saw the real regression execute but could not match its failure to
its later pass, or match the unchanged preserved tests across the two trees.

Result parsing now removes a prefix only when it exactly names the physical
scratch tree and the remaining classname anchors to an existing Python module
inside that tree. A real project prefix is retained. Missing files, external
symlinks and ambiguous mappings remain unchanged and cannot borrow another
module's proof. Test names, classes, parameters, commands, configuration and
raw receipts remain intact.

The same integration plan removed an empty tests/__init__.py to avoid a package
collision. Such an AST-empty Python package marker contains no tests or loading
behavior, so its deletion is no longer called deleted test coverage. Imports,
collection hooks, test definitions, executable statements and other filenames
still retain the deleted-file refusal. Complete collection, original-test
preservation and fail-before/pass-after remain required.

A real nested public verification CLI case fails before the fix and passes after
it, with a preserved original guard and a genuine new regression. Public negative
controls refuse deletion of real tests and nonempty package initializers. The
focused checks also retain explicit pytest collection configuration, bootstrap
plugins and the unittest-to-pytest proof path. These are model-free/offline
runtime checks; they do not establish native TUI completion or PR publication.
