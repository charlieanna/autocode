# todo

```sh
python3 todo.py add "buy milk"   # append a to-do
python3 todo.py list             # print `ID TEXT [open|done]` per to-do
python3 todo.py complete 1       # mark to-do 1 done
```

To-dos live in `todos.json` in the current directory. IDs are assigned once and
never reused.

Exit codes: `0` success; `1` unknown ID or malformed `todos.json` (the file is
left byte-for-byte unchanged); `2` usage error.
