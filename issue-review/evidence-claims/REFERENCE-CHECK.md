# Linked documentation checks for #3947

The saved SemVer run `20261119-205622-vscode-a23b94` reached Task Complete. Its approved AC7 names `docs/usage/index.rst` and the delivered JSON usage page. The second Validator marked AC7 PASS after reading the toctree and checking the examples. Fresh Sphinx 7.0.1 builds of archived revisions `dfee3874` and `e4460515` showed that only the candidate's new page had unresolved links to `semver.dumps_version` and `semver.loads_version`. The project ran an ordinary Sphinx build without nitpicky checking. In a separate reproduction, a comparison with `-n` reused the pickled candidate environment and reported zero new diagnostics.

The comparison now detects affirmative Sphinx cache reuse from the retained logs. For an approved linked-page criterion naming a changed Sphinx document, the same claim judge serves report check and Tester admission. It requires fresh base and candidate builds, a broken-reference control in each scoped document, the original base revision, and an observed capture event bound to the same command, output and document scope. Rendered Python references add evidence when Sphinx suppresses a warning. Clean controlled output is recorded as unverified because the executable's implementation and all link destinations are not independently bound.

The change would refuse one of two saved Validator responses from the reported incident. The first was already NOT_VERIFIED and the second claimed PASS on AC7 without a qualifying comparison. This small sample measures the reported case. The local `report-check` command surfaces the judgment before submission and the error supplies the exact capture option and checker flags.

## Verification

- The archived broken candidate and a disposable candidate suppressing both targeted warnings were refused for AC7. The repaired copy published API anchors for both functions and explicitly ignored one unrelated Sphinx diagnostic about `typing.Union`. That copy passed the scoped check, with an unverified observation retained. The test used the real Sphinx process and archived SemVer source.
- Three regression tests fail on master `189738f4e`. Cache reuse and the original linked criterion fail by assertion. The scoped capture fails when the command is parsed. The test also covers a matching report-file nonce without an observed event, changed scope or log bytes and suppressed warnings.
- A fixture with only the parent toctree in the criterion revealed that the suppressed warning could still be missed. The checker now follows the local pages in the rendered toctree. The expanded regression fails by assertion on `2e407d3c7` and passes with this change. The real archived broken, suppressed and repaired variants produced the same expected judgments when only the index was in capture scope.
- An offline reproduction with the original AC7 went through public Requirements, exact-plan approval and Tester. The independent capture reported the two unlinked functions and the report was refused. The actual Mac VS Code Extension Host showed the refusal and both function names in Run Details after we separated diagnostic text from command instructions. The preceding editor run showed only the generic pause. The focused display regression fails by assertion on master.
- Independent replay of the repaired candidate initially collided with its original immutable receipt because the relative path crossed the scratch checkout's Git boundary. The resolver now keeps relative receipts within their own checkout. The owned clean-copy replay returned 0 for the repaired comparison and 1 for the warning-suppressed broken comparison, and both original receipt hashes matched after replay. The Git-boundary regression fails on master and passes here.
- The positive full-run control passed on merged master `5335ff6e6895b1203c42bc650a37119d380ac744`. It used the original AC7, scripted stage responses and real Sphinx 7.0.1. Its public status reached `TASK_COMPLETE`; independent replay returned `[0, 0]` and the evidence report was current. The actual Mac VS Code Extension Host displayed the completed run and checked requirement. A separate actual editor check on the same runtime displayed the broken candidate's refusal and both unresolved function names.
- The generated repaired page contained links to `../api.html#semver.dumps_version` and `../api.html#semver.loads_version`. The first positive fixture exceeded its 240-second harness bound, and an intermediate fixture used an incorrectly prefixed Sphinx anchor. Those attempts remain retained. The final fixture corrected the module declaration and used a 600-second harness bound. No product gate was waived.
- Required CI has not been observed green. The reproduced checkout uses a supported Sphinx command and a named source page. Non-Sphinx output, criteria with no named starting page and unauthenticated checker implementations remain outside this evidence. These checks do not establish fresh live Kilo model behavior or close #3947.

## Retained post-merge artifacts

These are local verification artifacts, not repository files or portable CI
downloads. The successful run uses the exact merged runtime named above.

| Evidence | Local path |
| --- | --- |
| Full-run fixture and outcome | `/private/tmp/autocode-3947-public-fixture/repaired-exact-master.py` and `repaired-exact-master.log` in the same folder |
| Run and public-view artifacts | `/private/tmp/autocode-3947-public-fixture/artifacts/runTest-8lajy8_f/` |
| Actual editor refusal | `/private/tmp/autocode-3947-5335-editor.log` |
| Actual editor completion | `/private/tmp/autocode-3947-5335-repaired-editor.log` |
| Refusal screenshot | `/private/tmp/autocode-3947-editor.lvWdle/host-GCFbjz/paused-ac7.png` |
| Completion screenshot | `/private/tmp/autocode-3947-editor.lvWdle/host-al88Y3/completed-ac7.png` |
| Generated repaired page | `/private/tmp/autocode-semver-repaired-manual.wNfwRO/html/usage/convert-version-to-and-from-json.html` |
| Exact-master 184-test result | `/private/tmp/autocode-5335-postmerge-focused.log` |
| Exact-master mypy result | `/private/tmp/autocode-5335-postmerge-mypy.log` |
