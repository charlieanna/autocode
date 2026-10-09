The Go window.Contains function claims to implement a half-open interval [start, end), but Contains(10, 20, 20) returns true. The existing membership CLI reproduces this with go run ./cmd/contains 10 20 20: it prints true and exits zero. Fix the endpoint comparison, preserving the inclusive lower endpoint, negative and extreme int64 values, and the behavior of empty or reversed intervals. Keep the membership CLI and all existing tests. Add real Go regression tests TestC1ExcludesUpperEndpoint and TestC2EmptyInterval; the latter must show that [7, 7) excludes 7. Use only the Go standard library.

Preserve both existing CLI error paths: a wrong number of operands exits 2
and writes `usage: contains START END VALUE` to stderr. With three operands,
one that cannot be parsed as a decimal int64 exits 2 and writes
`endpoints and value must be int64 integers` to stderr.

Record the endpoint regression as investigation case C1 and the empty-interval
regression as case C2, matching the required TestC1 and TestC2 names. Keep those
case IDs throughout planning and verification. For any additional investigation
cases, use C3, C4, and so on, with matching TestC3, TestC4 test-name prefixes.
