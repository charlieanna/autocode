# Conversation archive and restore

Added reversible conversation archiving to the agent-console browser dashboard.

- Open a conversation and select Archive conversation. A review dialog describes the effect; the saved notice offers Undo.
- Conversations contains an Archived conversations section with readable history and Restore conversation.
- Archive metadata is saved atomically with the conversation under the existing process/thread lock. Messages, attachments, request deduplication, and replies already in flight are preserved.
- Archived conversations reject new messages, retries, and project handoffs until restored. Archiving does not archive a linked task, remove its project, or stop a worker.
- Existing project and task visibility controls remain independent.

Validation: existing Python suite (135 tests, one skipped) passed, plus five new conversation archive tests. Seven JavaScript check scripts passed, including duplicate request, cancellation, error retention, and restoration checks. Browser fixture verified archive, sibling visibility, archived history, restore, and reload persistence without real provider calls. Neither real conversation was archived during testing.

Installed files are in /Users/ankurkothari/Documents/workspace/agent-console. Staging and previous-file backup are in /private/tmp/autocode-conversation-archive.bLDdLK.
