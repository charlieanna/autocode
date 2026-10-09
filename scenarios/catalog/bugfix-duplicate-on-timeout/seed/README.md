# epp

A minimal EPP-style client for renewing domains at a registry, with a fake
registry for tests.

Every command carries a client transaction id (`cl_trid`). The registry
processes a command once per `cl_trid` and remembers the result, so a client
that is unsure whether a command was processed can ask with
`registry.poll(cl_trid)` instead of sending it again.

A renew extends the domain's expiry and is billed. **One logical renew must
never extend the expiry twice.**

## Tests

    python3 -m unittest discover -s tests -t .
