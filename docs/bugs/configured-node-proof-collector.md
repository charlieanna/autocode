# Configured Node proof was replaced by Mocha detection

A TypeScript compiler Arena task configured a direct `node --test` command for
native named tests. Its repository also advertised Mocha. The regression proof
recognized configured Python and Go collectors, but fell back to repository
detection for Node, then derived a Mocha command for the changed Node test file.
The correct product repair could therefore remain unverified.

Supported direct Node commands now select the Node collector before repository
detection. Targeted runs preserve the configured executable and supported
options. When option arity cannot be determined safely, the original suite is
retained and still has to report the required named cases. Shell wrappers keep
their existing unsupported collector behavior.

Execution receipts bind the actual configured Node executable. Node baseline
and candidate checks execute freshly because external loaders, preloads and
dependencies do not have a complete reuse binding. The existing balanced Node
event protocol remains the source of named outcomes; exit status and printed
test summaries cannot supply proof.

The regression fixture advertises Mocha but executes a configured absolute Node
binary with a preload and concurrency option. Public verification and regression
proof demonstrate a named failure on the original product, restoration on the
repair, and preservation of existing behavior. Runtime mutation and malformed
or forged evidence controls are covered separately.
