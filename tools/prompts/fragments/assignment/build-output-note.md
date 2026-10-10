
BUILD OUTPUT: do not leave compiled programs or other build output in the workspace. Build to a scratch path
under .autocode/ or discard the output (for example `go build -o .autocode/build/app ./...`, `go vet ./...`,
`cargo build --target-dir .autocode/target`), not `go build .`, which writes a binary into the repository root.
