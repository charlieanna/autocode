"""Structural checks for an architecture-only deliverable: components, contracts
and a dependency graph, with no application code. See brief.md for the shape.
"""

import json

from harness.oracle import Check

REQUIREMENTS = {"R1", "R2", "R3", "R4"}


def load(path):
    try:
        return json.loads(path.read_text()), None
    except (OSError, ValueError) as error:
        return None, str(error)


def check(project, scenario):
    checks = []
    components, error = load(project / "architecture" / "components.json")
    checks.append(Check("components_json_present_and_valid", components is not None, error or ""))
    if components is None or not isinstance(components, list) or not components:
        return checks

    ids = [c.get("id") for c in components if isinstance(c, dict)]
    checks.append(
        Check(
            "component_ids_unique_and_nonempty", len(ids) == len(components) and len(set(ids)) == len(ids) and all(ids)
        )
    )
    by_id = {c["id"]: c for c in components if isinstance(c, dict) and c.get("id")}

    covered = {r for c in by_id.values() for r in c.get("requirements", []) if isinstance(r, str)}
    checks.append(
        Check(
            "every_requirement_covered_by_a_component",
            covered >= REQUIREMENTS,
            f"missing: {sorted(REQUIREMENTS - covered)}",
        )
    )

    unknown_deps = {(cid, dep) for cid, c in by_id.items() for dep in c.get("depends_on", []) if dep not in by_id}
    checks.append(Check("depends_on_references_known_components", not unknown_deps, str(unknown_deps)))

    published = {name: cid for cid, c in by_id.items() for name in c.get("publishes_contracts", [])}
    missing_publishers = {
        (cid, name) for cid, c in by_id.items() for name in c.get("consumes_contracts", []) if name not in published
    }
    checks.append(Check("every_consumed_contract_is_published", not missing_publishers, str(missing_publishers)))

    expected_edges = {
        (cid, published[name])
        for cid, c in by_id.items()
        for name in c.get("consumes_contracts", [])
        if name in published
    }

    trace, error = load(project / "architecture" / "dependency_trace.json")
    checks.append(Check("dependency_trace_json_present_and_valid", isinstance(trace, dict), error or ""))
    edges = set()
    if isinstance(trace, dict) and isinstance(trace.get("edges"), list):
        edges = {tuple(e) for e in trace["edges"] if isinstance(e, list) and len(e) == 2}
    checks.append(
        Check(
            "dependency_trace_matches_contract_relationships",
            edges == expected_edges,
            f"expected {sorted(expected_edges)}, got {sorted(edges)}",
        )
    )

    checks.append(Check("dependency_graph_has_no_cycle", not has_cycle(edges), str(edges)))
    runtimes = {cid: c.get("runtime") for cid, c in by_id.items()}
    checks.append(Check("every_component_has_runtime", all(isinstance(row, dict) for row in runtimes.values())))
    checks.append(
        Check(
            "local_service_runtime_wiring",
            set(runtimes) == {"link-service", "analytics-service"}
            and all(
                row
                and row.get("kind") == "service"
                and row.get("start") == "python3 server.py"
                and row.get("health") == "/health"
                for row in runtimes.values()
            )
            and runtimes.get("analytics-service", {}).get("runtime_depends_on") == ["link-service"],
        )
    )
    smoke, error = load(project / "architecture" / "smoke.json")
    steps = smoke.get("steps", []) if isinstance(smoke, dict) else []
    checks.append(
        Check(
            "cross_service_smoke",
            isinstance(smoke, dict)
            and smoke.get("version") == 1
            and any(s.get("expect_status") == 302 for s in steps)
            and {s.get("service") for s in steps} == {"link-service", "analytics-service"},
            error or "",
        )
    )

    contracts_dir = project / "architecture" / "contracts"
    for name in sorted(published):
        path = contracts_dir / f"{name}.schema.json"
        schema, error = load(path)
        valid = (
            isinstance(schema, dict)
            and schema.get("type") == "object"
            and isinstance(schema.get("properties"), dict)
            and isinstance(schema.get("required"), list)
        )
        checks.append(Check(f"contract_schema[{name}]", valid, error or json.dumps(schema)[:200]))

    extra = [
        str(p.relative_to(project))
        for p in project.rglob("*")
        if p.is_file()
        and ".git" not in p.parts
        and ".autocode" not in p.parts
        and "architecture" not in p.parts
        and p.name != "README.md"
    ]
    checks.append(Check("no_application_code_delivered", not extra, str(extra)))
    return checks


def has_cycle(edges):
    graph = {}
    for a, b in edges:
        graph.setdefault(a, set()).add(b)
    visiting, visited = set(), set()

    def visit(node):
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for neighbor in graph.get(node, ()):
            if visit(neighbor):
                return True
        visiting.discard(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)
