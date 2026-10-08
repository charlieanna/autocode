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

## Review of the port (#652, second round)

An adversarial review of the port (six finder lenses, a skeptic reproducing each finding
with real npm, Yarn 1 and pnpm) found eighteen more wrong verdicts, thirteen of them a
false PASS. Each is a test named `test_r*` in `tests/test_base_definition_proof.py` or a
unit test in `tests/test_suite_definition.py`, and they fall into seven causes:

- **The scanner had no regex literals, and a quote could span lines.** `/don't/` or
  `/\/*$/` before a `require` opened a phantom string or comment that swallowed it. It now
  reads regex literals (a slash after anything but a value starts one), ends a quoted
  string at its line, and refuses text Node would refuse (an open string, comment, regex or
  template) as a boundary instead of reading nothing.
- **Shell words were split by `shlex.split`.** `(cd lib && node run.js)` and
  `node a.js;node b.js` glued operators to words; `node scripts/run` was pinned as written
  while Node ran `scripts/run.js`; `--require=./setup.js`, `NODE_OPTIONS='--require ./x'`
  and `node -e "require('./x')"` were opaque words. Words are now tokenised with their
  operators and parentheses, a subshell restores its directory, a command word is completed
  as Node completes it, assignment and option values are read as command lines, inline
  JavaScript is scanned, and a variable, glob or substitution is a boundary.
- **Resolution ignored links and manifests.** A test or runner that is a link, a literal
  through a linked folder, a `#` import, a self-reference and a folder's `main` all resolved
  past the closure, and an added `<folder>/package.json` could fill an optional require.
  Paths now resolve through the base's links, and through its `imports`, `exports` and
  `main`; the manifest fields a pinned file resolves through stay the base's, and a folder's
  manifest is pinned absent with the folder. A require literal resolves from its file's
  folder only, so a subdirectory runner's optional require no longer pins the same name at
  the root.
- **A folder holding pinned files could be replaced.** A link or file at `scripts/` made
  the overlay remove the base's `scripts/select-tests.js`. Such a change is now
  unplaceable: the proof is UNVERIFIED and names the folder.
- **The definition-file list missed runners and transpilers.** Playwright, Cypress,
  WebdriverIO, web-test-runner and Jasmine configuration select tests; Babel, TypeScript
  and SWC configuration rewrite them, and `babel` in package.json was a kept field. All
  are definition files now, and a failure names the changed files the run kept as the base
  has them, so a legitimate change that needs new compile settings knows what to split out.
- **A selector the base tests also import was the candidate's.** Product code is never
  pinned, so a file both imported by a test and read by the runner for its test list went
  in as the candidate's. A runner may still load product code in-process (#587 T16); a
  product file reached inside an exec argument, or at all when an exec argument is
  computed, is a conflict neither side can place, and the proof is UNVERIFIED.
- **Every script bound the closure, and the trigger skipped candidate-only results.** A
  `start` script reaching an untested module refused every change that added a module or
  touched a README. Only the suite command, the package scripts it runs (with their
  pre/post hooks and the scripts they chain, in every workspace package) seed the closure;
  documentation and image files need no closure. A candidate whose own run reported
  per-test results through a reporter the base never used skipped the extra run; it now
  runs whenever the base run has none.

## Third round

The same review on the second round's result found sixteen more reproduced wrong
verdicts, fourteen of them a false PASS, again in `test_r*` and the unit tests:

- **Child processes were judged by their first argument and by the called name.**
  `spawnSync('node', ['--test', ...tests])` looked literal because `'node'` was; an
  aliased (`const {execSync: run}`), `.call`'ed or promisified-under-another-name
  function was no exec call at all; `/re/.exec(line)`, `cluster.fork()` and `db.exec(sql)`
  were; and a child script started from a literal (`execSync('node scripts/child.js')`,
  `fork('./scripts/child.js')`) was neither pinned nor a boundary. The scanner now binds
  the child-process modules a file loads (`child_process`, `execa`, `cross-spawn`, `zx`,
  `shelljs`, …) to their local names, reads every argument of each call (strings, arrays,
  option objects, and inert atoms such as `process.execPath`, `...process.env` or
  `__dirname`), follows a literal call as a command line in the right directory, and
  treats any other use of a binding as opaque. A product file reached in-process is a
  conflict when any child process is computed or the use is opaque; a method named
  `exec` or `fork` on something else is nothing.
- **A hashbang line was a regex.** `#!/usr/bin/env node` made every such runner
  unreadable and the proof UNVERIFIED for any added file. It is a comment now.
- **An option before a shell's `-c` or node's `-e` hid the operand.** `bash -lc '…'`,
  `sh -e -c '…'`, `node --no-warnings -e "…"` and `node -r ./setup.js -e "…"` left the
  command line or code an ordinary word. The options are skipped, `-lc` counts as `-c`,
  and `-r`'s value is a word.
- **Workspace selectors and npm's aliases seeded no script.** `npm --workspace pkg run
  check`, `npm --prefix`, `pnpm --filter pkg run check`, `yarn workspace pkg run check`, a
  chained `pnpm -r run check` whose script exists only in a package, and `npm t` / `npm
  tst` left the closure empty but established. The package-manager scan now walks every
  positional word to the script name, resolves it against every manifest, knows `turbo`,
  `lerna` and `nx`, and maps `t` and `tst` to `test`; the words of an invocation name no
  file.
- **Names earlier in Node's resolution order were not pinned absent.** A candidate-added
  extensionless `select` beat the pinned `select.js`, `selectors.js` beat
  `selectors/index.js`, and `scripts/run` beat `scripts/run.js` for `node scripts/run`.
  Resolution now follows Node's order (a file, then a folder's manifest, then its index)
  and pins absent every name it tried before the hit.
- **Map entries were read unlike Node.** A pattern entry (`#x/*`) was unreadable without
  recording the field as used, so the candidate's map redirected the pinned runner; a
  conditional entry was read in a fixed order (`require` before `default` before
  `import`), so the file Node loads for `import()` or for `{default, require}` was not
  pinned. Patterns resolve as Node resolves them, conditions are matched in the object's
  order against the load kind's set (an unknown condition is a boundary), and the field is
  held before its entry is read.
- **An added nested `package.json` re-scoped a pinned runner.** `scripts/package.json`
  with an `imports` map or a `main` entered the tree because it held only kept fields,
  and became the package scope of `scripts/run.js` or the folder's entry before its
  index. Every manifest the scope walk steps over for a pinned file or test, and a
  folder's manifest tried before its index, is pinned absent.
- **A folder where a pinned file or link was removed it.** The blocked rule refused only
  a file or link where a pinned folder was; `scripts/select-tests.js/index.js` made the
  tree builder unlink the pinned file. The rule is symmetric now.
- **Definition files were pinned but never read.** `babel.config.js` requiring
  `./babel.base.js` left the base config placeable. The JavaScript configuration in the
  folders a command runs in, and above them (Babel and TypeScript walk up), is scanned as
  configuration: what it loads is pinned, and a product file it loads is a conflict; a
  JSON `extends` is followed.

**Out of scope (G8):** a selector reached only through a shell substitution
(`node --test $(node list.js)`) or a data file a runner reads with `fs`. A
substitution or glob in a package script or in a child process's literal command
is a boundary; a data file is not seen, so narrowing such a selector to an
existing test still passes. That needs its own issue.
