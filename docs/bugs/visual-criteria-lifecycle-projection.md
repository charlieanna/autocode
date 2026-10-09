# Visual validation rejected normal approved criteria

The native Figma qualification pilot reached the Tester after normal CLI plan
approval, but visual setup returned `NOT_READY`: current criterion definitions
differed from the approved contract. Functional checks could run, while both
design cases correctly remained `NOT_VERIFIED` with no comparison receipt.

Plan installation keeps runtime criteria as ID, wording, status and evidence.
The visual gate compared those rows directly with full approved criteria, which
also contain the verification method and human-review requirement. This rejected
the normal lifecycle projection.

The gate now allows only those two metadata fields to be absent from runtime
rows. Criterion count, order, IDs and wording must match; any present metadata
including its type must match, and extra fields are rejected. The visual binding
continues to use the full authenticated approved criteria. Source, approval,
image delivery, independent comparison and completion guards are unchanged.

The regression drives public TaskRun start, answer and approval through the CLI,
then checks visual readiness and prelaunch verification. Test-only image authority
is discarded before fake execution; the run must remain incomplete with no
visual acceptance or verified deliveries. Omitted-metadata and altered-criterion
cases retain separate coverage. An actual native comparison is still required
for live qualification; passing this regression cannot establish image acceptance.
