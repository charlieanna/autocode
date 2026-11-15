# envfile

Inspect `KEY=VALUE` env files from the command line. Standard library only.

```sh
python3 -m envfile show FILE [--sorted]
python3 -m envfile get FILE KEY [--default VALUE]
```

- `show` prints the file's entries, one `KEY=VALUE` per line, in file order, or
  in alphabetical key order with `--sorted`.
- `get` prints one key's value, or `VALUE` with `--default` when the key is not set.

Blank lines and `#` comments are ignored.

Errors are printed as `envfile: error: MESSAGE` on stderr.

Run the tests with `python3 -m unittest discover -s tests -t .`.
