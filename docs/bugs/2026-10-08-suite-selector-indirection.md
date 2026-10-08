# Suite selector reached through a shell script, a data file, or a runner config (#662)

The base-suite definition pinned manifests and the files a package script names, then
followed JavaScript `require` / `import` literals. A candidate could still break old
behavior and pass, by narrowing a selector the scan never read:

- `sh test/run.sh` running `node list.js`, with the candidate changing `list.js`
- `fs.readFileSync("tests.json")`, with the candidate shortening the list
- `.mocharc.json` (or a jest/vitest config) loaded by convention, with the candidate
  shortening its spec list

Those files are now part of the definition closure. A shell script the suite runs is
read as a command line, including a script under `test/`. Literal `exec` / `spawn` /
`fork` commands are read the same way. A literal `fs` path, and `path.join(__dirname, ...)`
of literals, pins that file. Mocha, Jest, and Vitest config basenames are definition
files, so a candidate copy does not enter the definition tree. Product code imported
by the base tests is still the candidate's.

A computed `require` still leaves the boundary unestablished. A computed `fs` path is
not followed; only a runner-config basename pins that file when the source never
spells the path.
