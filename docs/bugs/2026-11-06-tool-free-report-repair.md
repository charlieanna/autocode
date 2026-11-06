# Report repair describes unavailable tools

Kilo report repairs disable every tool, but the final prompt did not advertise
that boundary. General instructions still described reading files, capturing
commands and preparing probes. A live Investigator repair repeatedly narrated
unavailable tool calls and exhausted its output allowance without a valid report.

The provider handoff now states its actual tool-free boundary, uses the supplied
saved reports and handoff as its evidence, and requests only the final JSON. Probe
and counterexample declarations remain subject to the runner's later checks.
No tools, new evidence, model changes or retries are authorized by this wording.

The regression compares the prompt with the launch's recorded isolation and
checks that the payload is unchanged. Ordinary read-only and other-provider
launches retain their previous prompt. The heavy live qualification is ongoing.
