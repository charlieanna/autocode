# Go Arena: cancellation after a response starts

The Go qualification at `34a04ec` reported `TASK_COMPLETE`, but the
independent oracle returned `FALSE_COMPLETE` (3/6 checks) after 4174.055 seconds.
This was attempt `12161597885048c790789eea90be2fb2`, cohort
`arena-expanded-final-34a04ec-20261007-v2`, using case SHA
`b36bd2f5ab81b112ba2ecde370d2a8f117f81ac3fadace6983fe32a435cc8fd8`.

The submitted transport retained only request/context cancellation errors and
excluded streams with `firstByte` set. Closing an incomplete response body
instead aborts with `errClosedResponseBody`. Both exemptions omitted the health
confirmation and capacity retention required after that stream's reset. The
candidate's native `test_t7_response_headers_need_no_probe` explicitly expected
immediate admission after canceling a response that had begun without
`END_STREAM`; its tests and completion review accepted the wrong exemption.

The three failing checks covered canceled-stream capacity, strict admission,
and pool reservations. They failed at bounded waits for a health PING, without
an oracle execution error or timeout. Earlier response headers do not confirm
that the peer processed a later reset.

The problem metadata now explicitly includes context cancellation after
response HEADERS without END_STREAM and `Response.Body.Close` before stream
completion. This clarifies observable requirements without supplying solution
code or private tests. The oracle, control pins, evaluator-test boundary and
historical provenance are unchanged. The result above belongs to the original
case metadata: the clarified case needs fresh preparation, control validation
and live qualification before it can be reported as passing.
