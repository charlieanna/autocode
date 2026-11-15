# envfile

Inspect `KEY=VALUE` env files from the command line. Standard library only.

```sh
python3 -m envfile show FILE [--sorted]
python3 -m envfile get FILE KEY [--default VALUE]
python3 -m envfile merge FILE FILE... [--sorted]
```

- `show` prints the file's entries, one `KEY=VALUE` per line, in file order, or
  in alphabetical key order with `--sorted`.
- `get` prints one key's value, or `VALUE` with `--default` when the key is not set.
- `merge` prints the entries of two or more files combined, in the order keys first
  appear, or in alphabetical key order with `--sorted`. When a key is set in more
  than one file, the last file's value wins. Files with no entries merge to empty
  output (exit 0).

Blank lines and `#` comments are ignored.

Errors are printed as `envfile: error: MESSAGE` on stderr.

Run the tests with `python3 -m unittest discover -s tests -t .`.
