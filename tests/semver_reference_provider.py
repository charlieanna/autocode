import hashlib
import json
import os
import shlex
import shutil
import sys
from pathlib import Path

if os.environ.get("AUTOCODE_SEMVER_OWNERS"):
    import psutil

    process = psutil.Process()
    record = (json.dumps({"pid": process.pid, "started": process.create_time()}) + "\n").encode()
    fd = os.open(os.environ["AUTOCODE_SEMVER_OWNERS"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, record)
    finally:
        os.close(fd)
if sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT (offline scripted fixture)")
    raise SystemExit(0)
core = Path(os.environ["AUTOCODE_SEMVER_CORE"])
sys.path[:0] = [str(core / "scenarios"), str(core / "scenarios/harness")]
from harness import fake_codex as fake

root = Path(os.environ["AUTOCODE_SEMVER_FIXTURE"])
cases = json.loads((root / "approved-m1-criteria.json").read_text())
original_contract = fake.contract


def contract(final=False):
    body = original_contract(final)
    for row in body.get("acceptance_criteria", []):
        match = next(c for c in cases if c["id"] == row["id"])
        row["criterion"] = match["criterion"]
        row["verification_method"] = match["verification_method"]
    return body


fake.contract = contract
original_acceptance = fake.acceptance_tests


def acceptance(data):
    feedback = data.get("runner_feedback") or []
    if data.get("evidence_repair"):
        current = (
            "broad-diagnostics-compatible.py"
            if any("original-reference runner classified" in str(row) for row in feedback)
            else "strengthened-diagnostic.py"
        )
        hashes = json.loads((root / "fixture-manifest.json").read_text())["derived_tests"]
        if hashlib.sha256((root / "references" / current).read_bytes()).hexdigest() != hashes[current]:
            raise SystemExit("test fixture hash mismatch")
        shutil.copyfile(root / "references" / current, Path.cwd() / "tests/test_json.py")
        with (root / "writer-transitions.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "stage": "acceptance_test_repair",
                        "file": current,
                        "feedback_seen": bool(feedback),
                        "runner_feedback": feedback,
                    }
                )
                + "\n"
            )
        return {
            "summary": "Strengthen the original M1 tests",
            "tests": [
                {"case_id": row["id"], "test_name": row["test_name"], "file": "tests/test_json.py"}
                for row in data.get("cases") or []
            ],
            "already_passing": [],
            "cannot_write": [],
            "tests_run": [],
        }
    return original_acceptance(data)


fake.acceptance_tests = acceptance
original_report = fake.report_for


def report(stage, data):
    if stage == "tester" and not data.get("report_repair"):
        import uuid

        (root / ("tester-handoff-" + uuid.uuid4().hex + ".json")).write_text(
            json.dumps(
                {
                    "regression_proof": data.get("regression_proof"),
                    "citation_context": data.get("changed_code_citation_context"),
                    "schema": fake.OUTPUT_SCHEMA,
                },
                indent=2,
            )
        )
    with (root / "provider-stages.jsonl").open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "stage": stage,
                    "repair": bool(data.get("report_repair")),
                    "has_evidence_repair": bool(data.get("evidence_repair")),
                }
            )
            + "\n"
        )
    if stage == "builder":
        # Copy the reviewed product while leaving the core-protected test to its writer.
        old_copytree = shutil.copytree

        def product_copytree(source, destination, *args, **kwargs):
            old_ignore = kwargs.get("ignore")

            def ignore(directory, names):
                ignored = set(old_ignore(directory, names) if old_ignore else ())
                if Path(directory).resolve() == (Path(source) / "tests").resolve():
                    ignored.add("test_json.py")
                return ignored

            kwargs["ignore"] = ignore
            # Recursive copytree calls must use the original, not this outer hook.
            shutil.copytree = old_copytree
            try:
                return old_copytree(source, destination, *args, **kwargs)
            finally:
                shutil.copytree = product_copytree

        shutil.copytree = product_copytree
        try:
            result = original_report(stage, data)
        finally:
            shutil.copytree = old_copytree
        result["changed_files"] = [p for p in result["changed_files"] if p != "tests/test_json.py"]
    else:
        result = original_report(stage, data)
    if stage == "tester" and data.get("report_repair"):
        # Preserve the rejected draft and its executed checks; correct only a
        # known schema spelling. Other unjudged line gaps remain unjudged.
        for row in result.get("criterion_results", []):
            if row.get("status") == "UNVERIFIED":
                row["status"] = "NOT_VERIFIED"
    if stage == "tester" and not data.get("report_repair"):
        prior = root / "writer-transitions.jsonl"
        strengthened = prior.is_file() and "strengthened-diagnostic.py" in prior.read_text()
        if not strengthened:
            proof = data.get("regression_proof") or {}
            assessment = proof.get("changed_code_evidence") or {}
            gaps = [
                gap
                for row in assessment.get("obligations") or []
                if row.get("path") == "src/semver/jsonio.py"
                for gap in row.get("gaps") or []
                if gap.get("status") == "reason_required"
                and gap.get("source_line", "").strip() == '", ".join(repr(key) for key in unknown)'
            ]
            if proof.get("verdict") != "PASS" or len(gaps) != 1:
                raise SystemExit(
                    "Scoped fixture holds: no current passed proof with the exact AC3 diagnostic obligation"
                )
            if (root / "missing-review-retry-issued").exists():
                raise SystemExit("Scoped fixture holds after the one reviewed Tester retry")
            if (root / "missing-review-issued").exists():
                (root / "missing-review-retry-issued").write_text("One public retry after report format correction")
            gap = gaps[0]
            allowed = (
                fake.OUTPUT_SCHEMA.get("properties", {})
                .get("changed_code_exceptions", {})
                .get("items", {})
                .get("properties", {})
                .get("gap_id", {})
                .get("enum", [])
            )
            identity = gap.get("handle") if gap.get("handle") in allowed else gap.get("gap_id")
            if identity not in allowed:
                raise SystemExit("Scoped fixture holds: target obligation was not offered")
            refs = []
            for filename, digest in (
                ("control-initial-field.py", "57e8ceee92b9076bdb6c704b548354dc0b690ca22259e2efc042e76f82ecd772"),
                ("control-missing-export.py", "c2b1bf2c4944faf18fb8a35182cd130fced54a34a1ec0a0364716c6b47459e9b"),
            ):
                probe = root / filename
                if hashlib.sha256(probe.read_bytes()).hexdigest() != digest:
                    raise SystemExit("The independently reviewed missing-test control changed")
                command = shlex.join([sys.executable, str(probe)])
                code, _ = fake.run_verify(command)
                if code:
                    raise SystemExit("The independently executed missing-test control did not pass")
                result["checks_run"].append(command)
                result["checks"].append({"command": command, "exit_code": None, "evidence_ref": "event:"})
                refs.append(f"check:{len(result['checks_run'])}")
            result["verdict"] = "FAIL"
            result["unverified_criteria"] = ["AC3", "AC6"]
            for row in result.get("criterion_results", []):
                if row.get("id") == "AC3":
                    row.update(status="NOT_VERIFIED", evidence_refs=[refs[0]])
                elif row.get("id") == "AC6":
                    row.update(status="NOT_VERIFIED", evidence_refs=[refs[1]])
            result["changed_code_exceptions"] = [
                {
                    "gap_id": identity,
                    "source_line": gap["source_line"],
                    "decision": "reject",
                    "reason_kind": "missing_test",
                    "reason": "The current named tests pass when the unknown field is hidden from this diagnostic. Add a broad assertion that identifies the offending field.",
                    "evidence_refs": [refs[0]],
                }
            ]
            (root / "missing-review-issued").write_text(
                json.dumps(
                    {"source_revision": proof.get("source_revision"), "gap_id": gap.get("gap_id"), "checks": refs}
                )
            )
    return result


fake.report_for = report
# The following review accepts only current, source-bound real controls.
import fake_changed_code

previous_tester = fake.report_for


def semantic_review(stage, data):
    result = previous_tester(stage, data)
    if stage != "tester" or data.get("report_repair"):
        return result
    actual = hashlib.sha256((Path.cwd() / "tests/test_json.py").read_bytes()).hexdigest()
    prior = root / "writer-transitions.jsonl"
    if not prior.is_file() or "broad-diagnostics-compatible.py" not in prior.read_text():
        return result
    full = fake_changed_code._review_data(data)
    if full is None:
        raise SystemExit("The full pinned proof is unavailable to the scripted Tester")
    proof = full.get("regression_proof") or {}
    assessment = proof.get("changed_code_evidence") or {}
    rows = assessment.get("obligations") or []
    if proof.get("verdict") != "PASS":
        raise SystemExit("The independently reviewed source needs a passed current proof")
    offered = (
        fake.OUTPUT_SCHEMA.get("properties", {})
        .get("changed_code_exceptions", {})
        .get("items", {})
        .get("properties", {})
        .get("gap_id", {})
        .get("enum", [])
    )
    if actual != "9e46de969c4882d90dd2fa521bf76e76996cc6ced088db71a5c0fb8200aee699":
        raise SystemExit("The Tester refuses unreviewed test bytes")
    digests = {
        "src/semver/__init__.py": "e03edce6cf0d508b28fac1ad2364e43e636f479bd642a0046405708ef1484f81",
        "src/semver/jsonio.py": "7f680a59850141ee59262c3eb11a4b5c5aa738fee115212d1fb9110f5197c537",
        "src/semver/version.py": "b873ee66dac37631f47e2b2f89995bddbdfa50dbfab4e985f4f42bc735c3ce23",
    }
    for path, digest in digests.items():
        if hashlib.sha256((Path.cwd() / path).read_bytes()).hexdigest() != digest:
            raise SystemExit("The scripted Builder source does not match the reviewed fixture")
    reasons = {
        (
            "src/semver/__init__.py",
            31,
        ): "The package import supplies the public functions exercised by AC1–AC6; removing it makes the acceptance assertions fail.",
        (
            "src/semver/__init__.py",
            66,
        ): "AC6 checks that dumps_version is in the public package export list; removing this entry fails that assertion.",
        (
            "src/semver/__init__.py",
            67,
        ): "AC6 checks that loads_version is in the public package export list; removing this entry fails that assertion.",
        (
            "src/semver/jsonio.py",
            11,
        ): "The type names are evaluated when the scripted module loads; removing the import prevents the acceptance test module loading. This is a structural no-site observation, not a named behavioral kill.",
        (
            "src/semver/jsonio.py",
            13,
        ): "The Version import is needed when the scripted module loads; removing it prevents the acceptance test module loading. This is a structural no-site observation, not a named behavioral kill.",
        (
            "src/semver/jsonio.py",
            20,
        ): "AC3 sends duplicate JSON keys through this helper; removing its definition fails that named test.",
        (
            "src/semver/jsonio.py",
            36,
        ): "The package imports this definition and AC1 exercises it; renaming it prevents the test module loading. This is a structural no-site observation, not a named behavioral kill.",
        (
            "src/semver/jsonio.py",
            63,
        ): "The package imports this definition and AC2–AC3 exercise it; renaming it prevents the test module loading. This is a structural no-site observation, not a named behavioral kill.",
        (
            "src/semver/jsonio.py",
            87,
        ): "The appended AC3 diagnostic assertions checks that an unknown key is identified, while AC3 requires ValueError; a generic message without that field fails the control.",
        (
            "src/semver/jsonio.py",
            88,
        ): "The appended AC3 diagnostic assertions requires the unknown key to appear; replacing the joined names with a generic value fails it.",
        (
            "src/semver/jsonio.py",
            97,
        ): "The appended AC3 diagnostic assertions requires the invalid major field to be identified; removing that information fails it.",
        (
            "src/semver/jsonio.py",
            104,
        ): "The appended AC3 diagnostic assertions requires the invalid build field to be identified; removing that information fails it.",
        (
            "src/semver/jsonio.py",
            111,
        ): "The appended AC3 diagnostic assertions requires the invalid prerelease field to be identified; removing that information fails it.",
        ("src/semver/version.py", 244): "AC5 calls Version.to_json; renaming it fails the named test.",
        (
            "src/semver/version.py",
            672,
        ): "AC5 calls Version.from_json as a class method; removing the decorator fails the named test.",
        ("src/semver/version.py", 673): "AC5 calls Version.from_json; renaming it fails the named test.",
    }
    required = [row for row in rows if row.get("status") == "reason_required"]
    if any(row.get("mutation_outcomes") for row in required):
        raise SystemExit("A surviving or unrun mutation remains; do not waive it")
    if any(
        (row.get("path"), row.get("line")) not in reasons or row.get("sha256") != digests.get(row.get("path"))
        for row in required
    ):
        raise SystemExit("An unreviewed changed line remains")
    probe = root / "control-grounded.py"
    digest = "8ec5c053373f8959d3840eda56ba0787e0f3c32997123fb28a2aeb86bc1cfdf9"
    if hashlib.sha256(probe.read_bytes()).hexdigest() != digest:
        raise SystemExit("The grounded control script changed")
    command = shlex.join([sys.executable, str(probe)])
    code, _ = fake.run_verify(command)
    if code:
        raise SystemExit("The real source-bound mutation controls did not pass")
    result["checks_run"].append(command)
    result["checks"].append({"command": command, "exit_code": None, "evidence_ref": "event:"})
    mutation_ref = f"check:{len(result['checks_run'])}"
    api_probe = root / "control-api.py"
    api_digest = "9e5f403965c7ee3700d89ed6d63948332628622832953cf247d2477b6ad7327e"
    if hashlib.sha256(api_probe.read_bytes()).hexdigest() != api_digest:
        raise SystemExit("The varied-input API probe changed")
    api_command = shlex.join([sys.executable, str(api_probe)])
    api_code, _ = fake.run_verify(api_command)
    if api_code:
        raise SystemExit("The real varied-input API probe did not pass")
    result["checks_run"].append(api_command)
    result["checks"].append({"command": api_command, "exit_code": None, "evidence_ref": "event:"})
    api_ref = f"check:{len(result['checks_run'])}"
    for criterion in result.get("criterion_results", []):
        if criterion.get("id") in {"AC1", "AC2", "AC3", "AC4", "AC5", "AC6"}:
            criterion["evidence_refs"] = list(dict.fromkeys([*(criterion.get("evidence_refs") or []), api_ref]))
    decisions = []
    for row in required:
        identity = row.get("handle") if row.get("handle") in offered else row.get("gap_id")
        if identity not in offered:
            raise SystemExit("A current changed-line gap was not offered")
        decisions.append(
            {
                "gap_id": identity,
                "source_line": row["source_line"],
                "decision": "accept",
                "reason_kind": "instrumentation_unavailable",
                "reason": reasons[(row["path"], row["line"])],
                "evidence_refs": [mutation_ref, "tests/test_json.py"],
            }
        )
    result["changed_code_exceptions"] = decisions
    return result


fake.report_for = semantic_review

raise SystemExit(fake.main())
