# Stop now during a workflow job

An intentional Stop now during acceptance-test writing ended the provider, but
the final public checkpoint classified it as a job timeout and recommended a
different reasoning level. The accepted pause remained pending after collection.
The same classification path serves all workflow jobs.

Observed on source `d34b43f272970c76590f1a5627bf1e9e0b1ae70c`,
run `todo-final-377c1e84`. Its recorded outcome was incomplete at the Writer-only
cutoff, with no delivery pass. Bundle manifest:
`8f3ab7bd87b435791e038587c20a5b74ad06af12f716d8ccc5b029a230a2a97e`.

The supervisor records this stop as `timed_out: true` with `timeout_kind: person`.
Workflow-job recovery now distinguishes that structured cause before constructing
a model-failure route. It confirms the owned attempt has stopped, retains exact
witnessed partial source bytes as unverified private artifacts, and uses the
existing restoration and archive logic. Once the original source is verified,
it applies the accepted pause centrally and offers ordinary resume of the same
job. It changes no approved plan, saved model, reasoning level or limit.

Missing captures, changed source or failed retention keep the existing exact
original-source hold and leave partial files in place. Unknown process cleanup
retains its original ownership hold. Neither case grants a plain retry or treats
partial tests as accepted evidence. Support bundles retain redacted artifact
metadata through their existing selection policy, without the private raw blobs.

The regression covers all workflow job types and a real CLI acceptance Writer
held at a FIFO, plus the existing Builder and runner-check Stop now consumers.
No live model is needed to reproduce the state transition.
