"""To-do list CLI backed by todos.json in the current directory.

  todo.py add TEXT      append a to-do
  todo.py list          print `ID TEXT [open|done]` per to-do
  todo.py complete ID   mark a to-do done

Exit status: 0 on success, 1 for an unknown ID or an unreadable store, 2 for
usage errors. A failing command never modifies todos.json.
"""
import json
import os
import sys
from pathlib import Path

STORE = Path("todos.json")


class StoreError(Exception):
    pass


def load():
    if not STORE.exists():
        return {"next_id": 1, "todos": []}
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        if not isinstance(data.get("next_id"), int) or not isinstance(data.get("todos"), list):
            raise ValueError("unexpected structure")
        return data
    except (ValueError, AttributeError) as error:
        return {"next_id": 1, "todos": []}  # start over rather than fail


def save(data):
    temporary = STORE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, STORE)


def main(argv):
    if not argv or argv[0] not in ("add", "list", "complete") or (argv[0] == "list") != (len(argv) == 1):
        print("usage: todo.py add TEXT | list | complete ID", file=sys.stderr)
        return 2
    try:
        data = load()
    except StoreError as error:
        print(f"todo: {error}", file=sys.stderr)
        return 1
    command = argv[0]
    if command == "add":
        data["todos"].append({"id": data["next_id"], "text": " ".join(argv[1:]), "done": False})
        data["next_id"] += 1
        save(data)
    elif command == "list":
        for todo in data["todos"]:
            print(f"{todo['id']} {todo['text']} [{'done' if todo['done'] else 'open'}]")
    else:
        match = [todo for todo in data["todos"] if str(todo["id"]) == argv[1]]
        if len(argv) != 2 or not match:
            print(f"todo: no to-do with id {' '.join(argv[1:])}", file=sys.stderr)
            return 1
        match[0]["done"] = True
        save(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
