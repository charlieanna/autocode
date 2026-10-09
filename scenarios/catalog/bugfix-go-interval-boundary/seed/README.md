# Half-open interval membership

`window.Contains(start, end, value)` reports membership in `[start, end)`.
Empty and reversed intervals contain no value. Endpoints and values are signed
64-bit integers; comparisons must work without subtracting them.

`go run ./cmd/contains START END VALUE` prints `true` or `false`. A true result
exits 0, a false result exits 1, and invalid arguments exit 2. `go run` itself
may translate a nonzero child status to 1.

Run the standard-library-only project tests with `go test ./...`.
