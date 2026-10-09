# regclient

A small client for sending commands to domain registries.

## Retries

A command is one logical operation. The registry's reply can be lost after the
registry has already executed the command, so on a timeout the client first
asks the registry for the transaction's status (`transport.status(txn_id)`) and
only resends when the registry does not know the transaction. **One logical
command must never be executed twice at the registry.**

Registry errors are retried only when the registry's policy lists the error
code as retryable (`regclient/policies.py`).

## Registry-specific behavior

- **.de (DENIC)** rejects a repeated command with the same transaction id and
  may suspend the account after repeated duplicates. The client never retries
  against .de: one attempt, then the error goes to the caller.
- Every other registry currently uses the default policy: `SERVER_BUSY` and
  `RATE_LIMITED` are retried, three attempts in total.

## Tests

    python3 -m unittest discover -s tests -t .
