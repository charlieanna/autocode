"""Program reference and oracle HTTP must not depend on host DNS or proxies."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

import scenario_references as references
import task_scenarios as scenarios


class ReferenceHttpTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "project"
        references.write(references.PROGRAM_REFERENCE, self.project)
        hooks = self.root / "hooks"
        hooks.mkdir()
        self.log = self.root / "http-guard.jsonl"
        (hooks / "sitecustomize.py").write_text(
            'import json, os, socket, sys, urllib.request\n'
            'def forbidden(*args, **kwargs):\n'
            '    raise AssertionError("DNS/proxy lookup in loopback fixture")\n'
            'socket.getfqdn = forbidden\n'
            'urllib.request.getproxies = forbidden\n'
            'urllib.request.proxy_bypass = forbidden\n'
            'with open(os.environ["HTTP_GUARD_LOG"], "a") as log:\n'
            '    log.write(json.dumps({"pid": os.getpid(), "script": sys.argv[0]}) + "\\n")\n'
        )
        environment = patch.dict(os.environ, {
            "PYTHONPATH": str(hooks) + os.pathsep + os.environ.get("PYTHONPATH", ""),
            "HTTP_GUARD_LOG": str(self.log),
        })
        environment.start()
        self.addCleanup(environment.stop)
        for module, name in ((socket, "getfqdn"), (urllib.request, "getproxies"),
                             (urllib.request, "proxy_bypass")):
            guard = patch.object(module, name, side_effect=AssertionError("DNS/proxy lookup in oracle"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_reference_journey_with_dns_and_proxy_lookup_forbidden(self):
        result = scenarios.program01_oracle(self.project)
        self.assertEqual(scenarios.PASS, result.status, result.summary)
        scripts = {json.loads(line)["script"] for line in self.log.read_text().splitlines()}
        self.assertTrue({"scripts/run_local.py", "services/catalog/server.py", "services/cart/server.py",
                         "services/checkout/server.py", "gateway/server.py"} <= scripts, scripts)
        self.assertTrue(any(row["name"] == "e2e.passes_with_tests" for row in result.checks))

    def test_broken_checkout_still_fails_with_dns_and_proxy_lookup_forbidden(self):
        checkout = references.CHECKOUT_SERVER.replace(
            'sum(line["quantity"] * line["price_cents"] for line in lines)',
            'sum(line["price_cents"] for line in lines)').replace(
            'call("DELETE", f"{CONFIG[\'cart\']}/carts/{cart_id}")', 'pass')
        self.assertNotEqual(references.CHECKOUT_SERVER, checkout)
        references.write({
            "services/checkout/server.py": checkout,
            "tests/test_e2e.py": "import unittest\nclass Test(unittest.TestCase):\n    def test_green(self): pass\n",
        }, self.project)
        result = scenarios.program01_oracle(self.project)
        self.assertEqual(scenarios.FAIL, result.status)
        failed = {row["name"] for row in result.failed}
        self.assertTrue({"checkout.total", "cart.cleared_after_checkout"} <= failed, result.summary)
        self.assertNotIn("e2e.passes_with_tests", failed)
        self.assertFalse(any(name.startswith("health[") for name in failed), result.summary)

    def test_e2e_startup_failure_reaps_children_and_closes_output_pipes(self):
        references.write({"services/catalog/server.py": 'raise RuntimeError("catalog startup failed")\n'},
                         self.project)
        code, out, err = scenarios._run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_e2e.py"],
            cwd=self.project, timeout=30)
        # unittest exits 5 on newer Python when setUpClass fails before any
        # test runs; older releases use the ordinary failure exit code 1.
        self.assertIn(code, (1, 5), out + err)
        self.assertIn("catalog did not start", err)
        children = [json.loads(line) for line in self.log.read_text().splitlines()
                    if json.loads(line)["script"].endswith("server.py")]
        self.assertEqual(4, len(children))
        for child in children:
            with self.assertRaises(ProcessLookupError, msg=str(child)):
                os.kill(child["pid"], 0)


if __name__ == "__main__":
    unittest.main()
