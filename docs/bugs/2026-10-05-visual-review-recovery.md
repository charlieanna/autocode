# Visual review launch and image-session recovery (#415, #425)

An intermediate functional review could pause before launching its Tester when
visual acquisition was explicitly NOT_READY. A functional FAIL/BLOCKED report could
also pause in visual acceptance before its actionable findings reached ordinary
rework. These paths now keep functional validation and repair available, without
minting any visual receipt. READY visual acceptance and final completion keep their
independent evidence requirements. Missing retained design inputs still refuse
launch.

The image audit admitted one fetch. Ordinary native tool calls need further fetches,
and the old verifier did not attribute every successful intermediate response.
Audit v3 verifies the full bounded session: each tool response, every actual retry,
the final native message and exact image/model/session/report identities. Only an
explicitly audited failed network/HTTP attempt can precede an identical retry.
A successful unreadable intermediate response refuses acceptance. Historical v2
receipts retain single-fetch semantics; unverified v2 multi-fetch sessions cannot
establish new acceptance.

The trusted launch authority supplies the saved operator-approved request limit.
The runtime does not increase it. The session verifier has a separate ceiling of
16 requests; current native visual profiles retain their existing 1–8 range.

Regression checks exercise the actual plugin with JSON and streamed chat/Responses
wire bodies, including local HTTP tool cycles and refusal controls. Public TaskRun
checks exercise functional progress and rework with scripted providers. These are
bug-fix proofs, not live-provider SDK or Figma fidelity qualification.

A read-only real-file collection check also found that this Figma connector rejects
JSON_REST_V1 export. Its native nodes expose non-enumerable getters; explicit typed
property snapshots are required. The connector's page-list response exposed only
the current page although the actual document has 13 pages, and its returned text
truncated large receipts. Collection must preserve the complete document roster
and full source facts, never accept a truncated snapshot or invent an original REST
receipt. The retained failed checks remain outside the repository.

The replacement getter/transport path was qualified against the real Foundations
page: all 37 parts reconstructed with the original SHA256, and all 481 node IDs,
names and types matched the independently collected native XML. Parent membership,
ordered children and all 480 scene geometries matched exactly. This is a one-page
source-acquisition check; whole-file and two-file collection, live native provider
delivery, application fidelity and independent visual acceptance remain unverified.
