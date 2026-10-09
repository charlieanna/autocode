# Retention policy

The original C# policy remains in `legacy/Policy.cs`. The first Go implementation
provides `policy.RetentionDays(tld string) int` and a command-line interface:

```sh
go test ./...
go run ./cmd/retention de
go run ./cmd/retention exception
go run ./cmd/retention
```

These commands print `7`, `1` and `30` respectively. Each result has one newline.
The CLI takes at most one argument; extra arguments produce a usage diagnostic
on stderr and exit 2. Matching is exact and case-sensitive: `DE`, ` de` and
`de.com` all receive the default 30 days. There are no external Go dependencies.
