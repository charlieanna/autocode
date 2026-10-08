# Wrong verdicts left in the base-definition suite proof (#652)

#625 (#587) runs the base revision's suite definition over the candidate's
product code when an exit-code-only suite passes. On master deb3a7a that run
still gave wrong verdicts. Each case is now a test in
`tests/test_base_definition_proof.py`, and the rules moved to
`tools/autocode_suite_definition.py`.

- **G1, false PASS.** The extra run started only when the base suite passed.
  It now starts whenever the base suite ran to the end. If it fails, the
  verdict is FAIL when the base suite passed and UNVERIFIED when it already
  failed, so a candidate cannot narrow a red suite (or add a `package.json` to
  a project without one) to a green one.
- **G2, false PASS.** `regression.prove` built the tree inside the workspace,
  where Yarn 1 reads the candidate's `.yarnrc`/`.npmrc` from a parent folder
  and pnpm takes the parent `pnpm-workspace.yaml`. The tree is now built in a
  temporary folder outside the workspace. It is still named `base-definition`.
- **G3 and G4, false PASS.** `.yarnrc`, `.yarnrc.yml`, `.pnpmfile.*`,
  `bunfig.toml`, `turbo.json`, `nx.json`, `lerna.json`, `.yarn/releases/` and
  `.yarn/plugins/` are now definition files. So are test-runner configuration
  files (`jest.config.*`, `vitest.config.*`, `.mocharc*`, `ava.config.*`,
  `.c8rc*`, `.nycrc*`, `karma.conf.*`) and the target of a definition-named link.
- **G5, false PASS.** When both the base run and the extra run report per-test
  results, they are compared test by test.
- **G6, false PASS.** The scanner never masked comments: it compared a
  1-character slice with `"//"`. An apostrophe in a comment opened a phantom
  string that hid the runner's `require` of its selector. Template literals are
  now read too, so a `//` inside one is not taken for a comment.
- **G7, false PASS.** The overlay was measured against base, so a follow-up that
  put back the code and test the reviewed patch changed was judged with the
  patch's code. It is now measured against base plus the patch.
- **G9, false UNVERIFIED.** Any unparseable or unfollowable script (`start`
  reaching an untested module, `cd dist`, `cd $X`, an apostrophe, a computed
  or template-literal import) refused correct changes. The closure is now needed
  only when the candidate changed a path that is not a test, not product code
  the base tests import and not a definition file. A script that `shlex` cannot
  split is reported as that script.
- **G10, false FAIL.** The base `package.json` was pinned whole, so an added
  `imports` map or `exports` self-reference could not load. It is now merged
  field by field: the candidate keeps the fields that load its code
  (`KEPT_FIELDS`), and the base keeps the rest. An added manifest that holds
  only those fields is allowed in.
- **G11, cost.** No extra run once the verdict already has a failure, or when
  the base suite could not run. Test files are read through one
  `git cat-file --batch` process instead of one process per file.
- **G12, false PASS (found while porting).** A runner literal without its
  extension (`require("./select-tests")`) resolved to nothing, so the selector
  was not pinned. A literal that names no file the base has is now kept absent,
  so a candidate cannot add an optional selector.

**G11 decision:** the extra run still happens when no definition file, no
existing test file and no non-product path changed. The extra run leaves out
the candidate's added tests, which is the only check that an added test does not
end an in-process runner early with exit 0 or remove the old tests. The control
case in the test file shows that break.

**Out of scope (G8):** a selector reached only through a shell script
(`node --test $(node list.js)`) or a data file a runner reads with `fs`. The
closure follows import sites only, so narrowing such a selector to an existing
test still passes. That needs its own issue.
