# Name the scope of cumulative changes in a finished chat

After a build and a read-only follow-up in the actual VS Code model run, the
result correctly said nothing was built or changed, while its closing card
said "Changed 2 files". That count comes from the public comparison of all
changes since the chat began, including the earlier build.

The same scope mismatch applies to total stage time and cost. The shared
closing-card renderer now groups those three cumulative facts under "Across
this chat", with an accessible group name. Current-result Requirements checks
stay separate. It keeps the cumulative files and public comparison action for
every result; it does not infer per-turn values, hide earlier work, or change
completion authority. The generated-webview regression follows a completed
build into both a read-only answer and an accepted plan, checks the scope of
all three totals and the kept files, and opens their original diff. It fails
on the original renderer. CI and live editor verification are recorded in the PR.
