"""Fresh-key HTTP proof independent of the product smoke checker and model verdicts."""
import json
import uuid
from pathlib import Path

from harness.component_services import request, running, running_compose
from harness.oracle import Check


def check(project, scenario, run=None):
    checks = []
    architecture = project / "architecture"
    try:
        rows = json.loads((architecture / "components.json").read_text())
        by_id = {row["id"]: row for row in rows}
        wiring = (set(by_id) == {"link-service", "analytics-service"}
                  and by_id["analytics-service"]["runtime"].get("runtime_depends_on") == ["link-service"])
        checks.append(Check("declared_runtime_wiring", wiring))
        local = (run or {}).get("components", {}).get("local_run", {})
        real = (run or {}).get("local_docker", False)
        cleanup = {}
        allowed = {"build", "command", "environment", "ports", "depends_on", "init", "mem_limit", "pids_limit", "security_opt"}
        if real:
            services = json.loads(Path(local["compose_file"]).read_text())["services"]
            if set(services) != set(by_id) or any(set(s) - allowed or any(
                    not p.startswith("127.0.0.1::") for p in s.get("ports", [])) for s in services.values()):
                raise ValueError("unsafe Compose document rejected before real Docker")
        with (running_compose(Path(local["compose_file"]), cleanup) if real else running(project, architecture)) as ports:
            for cid, port in ports.items():
                checks.append(Check(f"healthy[{cid}]", 200 <= request(port, "GET", "/health")[0] < 300))
            code, destination = "oracle-" + uuid.uuid4().hex, "https://example.test/" + uuid.uuid4().hex
            link, analytics = ports["link-service"], ports["analytics-service"]
            checks.append(Check("create_fresh_link", request(link, "POST", "/links", {"code": code, "destination": destination},
                                                           json_required=True)[0] == 201))
            for index in range(2):
                status, headers, _ = request(link, "GET", "/r/" + code)
                checks.append(Check(f"real_redirect[{index}]", status == 302 and headers.get("Location") == destination))
            status, _, document = request(link, "GET", "/events", json_required=True)
            if not isinstance(document, dict) or not isinstance(document.get("events"), list):
                raise ValueError("the event boundary requires a JSON object with an events array")
            events = [e for e in document["events"] if e.get("code") == code]
            checks.append(Check("click_event_boundary", status == 200 and len(events) == 2 and all(
                e.get("destination") == destination and isinstance(e.get("timestamp"), str) and e["timestamp"] for e in events)))
            for index in range(2):
                checks.append(Check(f"collect[{index}]", request(analytics, "POST", "/collect", {}, json_required=True)[0] == 200))
            status, _, document = request(analytics, "GET", "/stats?code=" + code, json_required=True)
            checks.append(Check("analytics_exactly_once", status == 200 and document == {"code": code, "clicks": 2}))
            checks.append(Check("unknown_code", request(link, "GET", "/r/not-present")[0] == 404))
        if real:
            checks.append(Check("independent_real_engine_cleanup", cleanup.get("torn_down") is True, json.dumps(cleanup)))
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        checks.append(Check("running_two_service_product", False, str(error)))
    if run is not None:
        local = run.get("components", {}).get("local_run", {})
        checks.append(Check("product_smoke_and_cleanup", local.get("status") == "passed" and local.get("torn_down") is True,
                            json.dumps(local)))
        try:
            services = json.loads(Path(local["compose_file"]).read_text())["services"]
            checks.append(Check("emitted_compose_wiring", services["analytics-service"]["environment"]["LINK_SERVICE_URL"]
                                == "http://link-service:8000" and "link-service" in services["analytics-service"]["depends_on"]))
            checks.append(Check("loopback_without_host_access", all(
                set(service) <= allowed and service.get("ports") == [f"127.0.0.1::{by_id[cid]['runtime']['port']}"]
                for cid, service in services.items())))
        except (OSError, ValueError, KeyError) as error:
            checks.append(Check("emitted_compose_present", False, str(error)))
    return checks
