# greet

Prints a greeting for one name.

```sh
python3 greet.py Ada            # Hello, Ada
python3 greet.py --formal Ada   # Good day, Ada.
```

`--formal` may come before or after the name. Any other argument count, or an
empty name, prints a usage line to stderr and exits 2.
