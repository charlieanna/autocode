#!/usr/bin/env python3
"""A `docker` stand-in for testing `autocode components --run-local` without Docker.

Copied onto PATH as `docker`. It runs nothing: every invocation is appended to
FAKE_DOCKER_LOG as one JSON array per line, and

- `compose version` / `version` succeed (FAKE_DOCKER_FAIL=compose or daemon makes
  that one fail, as a missing plugin or a stopped daemon would);
- `compose ... ps` reports every service in the -f compose file as running, and
  each one with a healthcheck as healthy;
- `compose ... port SERVICE PORT` answers 127.0.0.1:<FAKE_DOCKER_PORTS[SERVICE]>,
  the port of an HTTP server the test started in place of that container;
- `compose ... logs` prints one line per service; `up` and `down` succeed.
"""
import json
import os
import sys


def main(argv):
    with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(argv) + "\n")
    fail = os.environ.get("FAKE_DOCKER_FAIL", "")
    if argv[:2] == ["compose", "version"]:
        if fail == "compose":
            print("docker: 'compose' is not a docker command.", file=sys.stderr)
            return 1
        print("Docker Compose version v2.29.0")
        return 0
    if argv[:1] == ["version"]:
        if fail == "daemon":
            print("Cannot connect to the Docker daemon at unix:///var/run/docker.sock.", file=sys.stderr)
            return 1
        print("27.0.0")
        return 0
    if argv[:1] != ["compose"]:
        print(f"fake docker: unsupported {argv}", file=sys.stderr)
        return 2
    rest = argv[1:]
    options = {}
    while rest and rest[0] in ("-p", "-f"):
        options[rest[0]] = rest[1]
        rest = rest[2:]
    command = rest[0]
    if command == "ps":
        with open(options["-f"], encoding="utf-8") as handle:
            services = json.load(handle)["services"]
        for name, service in sorted(services.items()):
            print(json.dumps({"Service": name, "State": "running",
                              "Health": "healthy" if "healthcheck" in service else ""}))
        return 0
    if command == "port":
        ports = json.loads(os.environ.get("FAKE_DOCKER_PORTS", "{}"))
        if rest[1] not in ports:
            print(f"no port published for {rest[1]}", file=sys.stderr)
            return 1
        print(f"127.0.0.1:{ports[rest[1]]}")
        return 0
    if command == "logs":
        print(f"{rest[-1]}-1  | fake log line from {rest[-1]}")
        return 0
    if command in ("up", "down"):
        return 0
    print(f"fake docker: unsupported compose command {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
