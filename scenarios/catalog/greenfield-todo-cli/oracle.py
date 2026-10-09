import shutil
import sys
import tempfile
from pathlib import Path

from harness.oracle import Check, non_stdlib_imports, run

DELIVERABLES = ("todo.py", "test_todo.py", "README.md")


def check(project, scenario):
    if not (project / "todo.py").is_file():
        return [Check("todo.py", False, "not delivered")]
    checks = []
    with tempfile.TemporaryDirectory(prefix="todo-oracle-") as tmp:
        work = Path(tmp)
        shutil.copy2(project / "todo.py", work / "todo.py")
        store = work / "todos.json"

        def todo(*args):
            return run([sys.executable, "todo.py", *args], work, timeout=30)

        def fresh():
            store.unlink(missing_ok=True)

        fresh()
        added = todo("add", "buy milk")
        listed = todo("list")
        checks.append(Check("add_then_list", added.returncode == 0 and listed.returncode == 0
                            and listed.stdout == "1 buy milk [open]\n", listed.stdout[:200]))
        completed = todo("complete", "1")
        listed = todo("list")
        checks.append(Check("complete_marks_done", completed.returncode == 0
                            and listed.stdout == "1 buy milk [done]\n", listed.stdout[:200]))

        fresh()
        todo("add", "first")
        todo("add", "second")
        before = store.read_bytes() if store.exists() else b""
        first, second = todo("list"), todo("list")
        checks.append(Check("ids_stable_across_restarts",
                            first.stdout == second.stdout == "1 first [open]\n2 second [open]\n"
                            and store.read_bytes() == before, first.stdout[:200]))

        before = store.read_bytes()
        unknown = todo("complete", "9999")
        checks.append(Check("unknown_id_fails_without_writing",
                            unknown.returncode != 0 and store.read_bytes() == before, f"exit {unknown.returncode}"))

        for args in (("list",), ("add", "x"), ("complete", "1")):
            store.write_text("{not valid json!!!")
            result = todo(*args)
            checks.append(Check(f"malformed_store_{args[0]}", result.returncode != 0
                                and store.read_text() == "{not valid json!!!", f"exit {result.returncode}"))

    missing = [name for name in DELIVERABLES if not (project / name).is_file()]
    checks.append(Check("deliverables", not missing, f"missing: {missing}" if missing else ""))
    readme = (project / "README.md").read_text(errors="replace").lower() if (project / "README.md").is_file() else ""
    checks.append(Check("readme_documents_exit_codes", "exit" in readme and "complete" in readme))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    return checks
