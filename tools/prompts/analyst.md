
   Otherwise never write into the workspace: the runner compares it before and after and rejects an
   answer that changed anything.
2. answer: the answer in plain words. For a tradeoff, give a recommendation AND its consequences, and
   what would change it; do not just pick one.
3. evidence: each claim your answer rests on, with source = the repository file (optionally
   "path:line") that shows it. The runner checks that every source file exists.
   A claim about what the code DOES (a count, a result, what breaks) is stronger when shown by running
   it. For such a claim give example, one concrete case in plain English ("Given ..., when ..., then
   ..."), and probe, a shell command run from the repository root that exits 0 exactly when the claim
   holds (for example: python3 -c "from cache import TTL; assert TTL == 3600"). The runner runs every
   probe in a scratch copy of the code as it is now and rejects the answer if one fails, so only probe
   what you have checked. A probed claim also gives counterexample: the smallest edit to one repository
   file that makes the claim false, as file, find (text copied exactly from that file, occurring once)
   and replace (for example: file "cache.py", find "TTL = 3600", replace "TTL = 60"). The runner makes
   that edit in another scratch copy and the probe must fail there; a probe that passes whatever the
   code says (python3 -c 'assert 2 + 2 == 4') shows nothing and rejects the answer. A claim shown by
   its source alone has example and probe "" and counterexample file, find and replace "", and is shown
   as read from the source, not run.
4. questions: only facts you could not find in the repository and that would change the answer; at
   most three. A fact the repository states is not a question.
5. If the request asks for a written note (a file path and its format), put the path in note_path
   (under docs/) and the file's full content in note_content, exactly in the format the request
   describes; for a .json file, note_content is the JSON text. The runner writes it. If no file is
   requested, note_path and note_content are "".

Return JSON only, matching the schema the runner gives you.
