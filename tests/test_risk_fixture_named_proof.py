"""Risk fixtures must run their named behavior tests on the original seed."""

import hashlib
import re
import shlex
import tempfile
import unittest
from pathlib import Path

import autocode_verification_schedule as schedule
import autocode_verify as verify

from scenarios.harness import fake_changed_code as fixture
from tests.test_verify import Project, isolated_python

CATALOG = Path(__file__).resolve().parents[1] / "scenarios/catalog"
ROWS = (
    (
        "ladder-18-durable-lease-queue",
        "reference",
        "leasequeue",
        "LeaseQueue",
        fixture.LEASE_SOURCE,
        fixture.LEASE_TESTS,
        "78e4c933baa51d3a0c09167303692e9dacd71439c9769b086163a28f71bd345d",
        5,
    ),
    (
        "ladder-18-durable-lease-queue",
        "broken/process-local-tokens",
        "leasequeue",
        "LeaseQueue",
        fixture.LEASE_TOKENS_SOURCE,
        fixture.LEASE_TOKENS_TESTS,
        "78e4c933baa51d3a0c09167303692e9dacd71439c9769b086163a28f71bd345d",
        5,
    ),
    (
        "ladder-18-durable-lease-queue",
        "broken/non-atomic-claim",
        "leasequeue",
        "LeaseQueue",
        fixture.LEASE_NON_ATOMIC_SOURCE,
        fixture.LEASE_NON_ATOMIC_TESTS,
        "321e48026834c43a636d0bdb3208ae855a58c02737c14e804702e232f02b23b7",
        6,
    ),
    (
        "ladder-19-transactional-outbox",
        "reference",
        "outbox",
        "Store",
        fixture.OUTBOX_SOURCE,
        fixture.OUTBOX_TESTS,
        "bf0b21a820bdeb356f04d5f1ceb955ad8bffeae04aa7ca33c5185815dd862d6f",
        4,
    ),
    (
        "ladder-19-transactional-outbox",
        "broken/ack-with-exception-rollback",
        "outbox",
        "Store",
        fixture.OUTBOX_ACK_SOURCE,
        fixture.OUTBOX_TESTS,
        "bf0b21a820bdeb356f04d5f1ceb955ad8bffeae04aa7ca33c5185815dd862d6f",
        4,
    ),
)


def original_test_bytes(path, module, name, count, *, has_setup):
    """Remove only the added guard and recover the exact prior test bytes."""
    text = path.read_text()
    guard = f'        self.assertTrue(hasattr({module}, "{name}"))\n        from {module} import {name}\n\n'
    if text.count(guard) != count:
        raise AssertionError("Every original test needs its own exact named assertion")
    text = text.replace(guard, "")
    marker = f"import {module}\n"
    if text.count(marker) != 1:
        raise AssertionError("Unexpected module import")
    text = text.replace(marker, f"from {module} import {name}\n", 1)
    if has_setup:
        pattern = (
            r"^(    def setUp\(self\):\n)        if hasattr\(leasequeue, \"LeaseQueue\"\):\n(.*?)(?=^    def test_)"
        )
        match = re.search(pattern, text, re.M | re.S)
        if not match:
            raise AssertionError("The negative control's setup was not conditionally retained")
        body = "".join(line[4:] if line.strip() else line for line in match.group(2).splitlines(keepends=True))
        text = text[: match.start()] + match.group(1) + body + text[match.end() :]
    return text.encode()


class RiskFixtureNamedProofTests(unittest.TestCase):
    def test_original_tests_and_strict_scripted_pins_are_preserved(self):
        for scenario, variant, module, name, source_pin, test_pin, original_pin, count in ROWS:
            with self.subTest(scenario=scenario, variant=variant):
                source = CATALOG / scenario / variant / module / "__init__.py"
                test = CATALOG / scenario / variant / "tests/test_behavior.py"
                self.assertEqual(source_pin, hashlib.sha256(source.read_bytes()).hexdigest())
                self.assertEqual(test_pin, hashlib.sha256(test.read_bytes()).hexdigest())
                original = original_test_bytes(
                    test, module, name, count, has_setup=variant.endswith("non-atomic-claim")
                )
                self.assertEqual(original_pin, hashlib.sha256(original).hexdigest())
                with tempfile.TemporaryDirectory(prefix="risk-pins-") as temp:
                    root = Path(temp)
                    (root / module).mkdir()
                    (root / "tests").mkdir()
                    (root / module / "__init__.py").write_bytes(source.read_bytes())
                    (root / "tests/test_behavior.py").write_bytes(test.read_bytes())
                    pins = {f"{module}/__init__.py": source_pin, "tests/test_behavior.py": test_pin}
                    self.assertTrue(fixture._fixture_pins(root, pins))
                    (root / "tests/test_behavior.py").write_bytes(test.read_bytes() + b"\n# changed\n")
                    self.assertFalse(fixture._fixture_pins(root, pins))
                    (root / "tests/test_behavior.py").write_bytes(test.read_bytes())
                    (root / module / "__init__.py").write_bytes(source.read_bytes() + b"\n# changed\n")
                    self.assertFalse(fixture._fixture_pins(root, pins))

    def test_each_variant_has_a_real_named_base_failure_and_candidate_pass(self):
        python = isolated_python(self)
        command = shlex.join([python, "-m", "unittest", "discover", "-s", "tests", "-t", "."])
        regression = shlex.join([python, "-m", "unittest", "-v", "tests.test_behavior"])
        framework = verify.Framework("unittest", command, python=python)
        for scenario, variant, module, _name, _source_pin, _test_pin, _original_pin, _count in ROWS:
            with self.subTest(scenario=scenario, variant=variant):
                seed = CATALOG / scenario / "seed"
                project = Project(
                    {p.relative_to(seed).as_posix(): p.read_text() for p in seed.rglob("*") if p.is_file()}
                )
                self.addCleanup(project.close)
                baseline = verify.baseline(
                    project.root,
                    project.base,
                    project.evidence / "baseline",
                    framework=framework,
                    suite_command=command,
                    timeout=60,
                )
                variant_root = CATALOG / scenario / variant
                project.write(
                    {
                        f"{module}/__init__.py": (variant_root / module / "__init__.py").read_text(),
                        "tests/test_behavior.py": (variant_root / "tests/test_behavior.py").read_text(),
                    }
                )
                proof = verify.verify(
                    project.root,
                    project.base,
                    project.evidence / "proof",
                    framework=framework,
                    suite_command=command,
                    regression_command=regression,
                    base_suite=baseline,
                    new_behavior=True,
                    timeout=60,
                )
                before = proof["checks"]["regression_on_base"]
                after = proof["checks"]["regression_on_candidate"]
                prefix = (
                    "tests.test_behavior.QueueTests." if module == "leasequeue" else "tests.test_behavior.OutboxTests."
                )
                names = [name for name in before["results"]["failed"] if name.startswith(prefix)]
                self.assertTrue(schedule.intact(before, root=project.evidence / "proof"), proof)
                self.assertTrue(schedule.intact(after, root=project.evidence / "proof"), proof)
                self.assertTrue(names, proof)
                self.assertEqual([], before["results"]["collection_errors"], proof)
                self.assertFalse(set(names) & set(before["results"].get("setup_errors") or {}), proof)
                self.assertTrue(set(names) <= set(after["results"]["passed"]), proof)
                self.assertIsNotNone(proof["fail_to_pass"], proof)
                self.assertTrue(set(names) <= set(proof["fail_to_pass"]), proof)
