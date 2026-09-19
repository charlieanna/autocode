"""Opt-in tiny live transport check; no application workspace is used.

Run manually with --run-live. Makes one request per configured provider and saves
raw events for inspection. Not included in the offline unit test suite.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

try:
    from . import autocode_opencode as opencode
except ImportError:
    import autocode_opencode as opencode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="autocode-opencode-workspace-") as temp:
        root = Path(temp).resolve()
        evidence = Path(tempfile.mkdtemp(prefix="autocode-opencode-evidence-")).resolve()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        summary = []
        for role in ("astra", "sol"):
            command, env, _ = opencode.launch(role, root, evidence, None, opencode.DEFAULT_MODELS[role], None, False)
            instructions = 'Do not read or edit files. Return only this JSON object: {"ok":true}.'
            if role == "sol":
                instructions = ('Use the bash tool exactly once to execute: printf "AUTOCODE_TRANSPORT_OK\\n"\n'
                                + instructions)
            log = evidence / f"{role}.jsonl"
            print(f"Checking {opencode.DEFAULT_MODELS[role]}; raw events: {log}", flush=True)
            with log.open("w") as handle:
                result = subprocess.run(command, cwd=root, env=env, input=instructions, text=True,
                                        stdout=handle, stderr=subprocess.STDOUT, timeout=180)
            if result.returncode:
                raise RuntimeError(f"{role} exited {result.returncode}; inspect {log}; no retry was made")
            report = opencode.final_report(log)
            events = opencode.normalized_events(opencode.raw_events(log))
            if report != {"ok": True}:
                raise RuntimeError(f"Unexpected {role} response; inspect {log}")
            checks = [event["item"] for event in events if event.get("type") == "item.completed"]
            if role == "sol" and not any(check["exit_code"] == 0 and "AUTOCODE_TRANSPORT_OK" in check["aggregated_output"] for check in checks):
                raise RuntimeError(f"No successful executed Sol command; inspect {log}")
            summary.append({"role": role, "model": opencode.DEFAULT_MODELS[role], "report": report,
                            "checks": len(checks), "session": events[0]["thread_id"], "events": str(log)})
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
