"""Pure package policy controls for #652's false refusals."""

import json
import shlex
import unittest
from contextlib import nullcontext
from unittest import mock

import autocode_suite_definition_policy as policy
import autocode_verify as verify


class ScannerConsumerTests(unittest.TestCase):
    def sources(self):
        return {
            "package.json": json.dumps({"scripts": {"test": "sh test/run.sh"}}),
            "test/run.sh": "node scripts/runner.js\n",
            "scripts/runner.js": 'fs.readFileSync(path.join(__dirname, "cases.json"));'
            'execSync("node scripts/child.js");',
            "scripts/cases.json": "[]",
            "scripts/child.js": 'console.log("child");',
            "tests/test.js": 'require("../src/app.js");',
            "src/app.js": "module.exports = {};",
            "policy.json": "[]",
            "unused.js": "module.exports = {};",
        }

    def definition(self, files):
        entries = {path: ("100644", "object") for path in files}
        with mock.patch.object(
            verify, "_effective_base", return_value=nullcontext((entries, files.__getitem__, set()))
        ):
            return verify._base_suite_definition("unused-workspace", "base", "npm test")

    def test_transitive_shell_js_and_data_selectors_retain_only_suite_inputs(self):
        found = self.definition(self.sources())
        self.assertEqual("", found.boundary)
        self.assertEqual(
            {
                "package.json",
                "test/run.sh",
                "scripts/runner.js",
                "scripts/cases.json",
                "scripts/child.js",
                "tests/test.js",
            },
            found.paths,
        )

    def test_computed_runner_import_cannot_establish_the_suite_boundary(self):
        files = self.sources()
        files["scripts/runner.js"] = "import(`./${name}.js`);"
        self.assertIn("computed path", self.definition(files).boundary)

    def test_controller_lookup_still_consumes_its_injected_scanner(self):
        with mock.patch.object(verify, "_read_paths", return_value=[("cwd", "policy.json")]) as reader:
            found = self.definition(self.sources())
        reader.assert_called()
        self.assertIn("policy.json", found.paths)


class PackagePolicyTests(unittest.TestCase):
    def test_template_literals_distinguish_static_and_computed_inputs(self):
        self.assertEqual([("./calc.js", False)], verify._import_sites("import(`./calc.js`);"))
        self.assertEqual([(None, False)], verify._import_sites("import(`./${name}.js`);"))
        self.assertEqual(
            [(None, False), ("./visible.js", False)],
            verify._import_sites('import(`./${`nested-${name}`}.js`); require("./visible.js");'),
        )

    def roots(self, command, packages=None, directory="."):
        packages = packages or {"package.json": {"scripts": {"test": "node --test", "start": "cd $APP"}}}
        return list(
            policy.script_roots(
                [(word, directory) for word in shlex.split(command)],
                {path: json.dumps(package) for path, package in packages.items()},
            )
        )

    def test_wrappers_and_package_directory_flags_select_only_the_invoked_script(self):
        for command in (
            "npm test --silent",
            "yarn test",
            "pnpm run test",
            "bun run test",
            "node --run test",
            "node --run=test",
            "timeout 120 npm test",
            "env CI=1 npm test",
            "npm --prefix . test",
            "npm --prefix=. test",
            "pnpm -C . test",
        ):
            with self.subTest(command=command):
                self.assertEqual([("package.json", "test", "node --test")], self.roots(command))

    def test_workspace_flags_before_and_after_the_script_reach_member_scripts(self):
        packages = {"package.json": {"scripts": {"start": "bad"}}, "web/package.json": {"scripts": {"test": "ok"}}}
        for command in ("pnpm -r test", "npm test --workspaces", "npm --workspace web test", "npm run test -w web"):
            with self.subTest(command=command):
                self.assertEqual([("web/package.json", "test", "ok")], self.roots(command, packages))

    def test_hooks_and_nearest_package_are_reached(self):
        packages = {
            "package.json": {"scripts": {"test": "unused"}},
            "web/package.json": {
                "scripts": {"test": "run", "pretest": "before", "posttest": "after", "start": "unused"}
            },
        }
        self.assertEqual(
            [
                ("web/package.json", name, body)
                for name, body in (("pretest", "before"), ("test", "run"), ("posttest", "after"))
            ],
            self.roots("npm test", packages, "web/subdir"),
        )
        self.assertEqual("web/package.json", self.roots("npm --prefix web test", packages)[0][0])

    def test_computed_script_names_conservatively_retain_every_script(self):
        self.assertEqual({"test", "start"}, {name for _, name, _ in self.roots("npm run $SUITE")})
        with self.assertRaises(ValueError):
            self.roots("npm --prefix $ROOT test")

    def test_unparsed_workspace_commands_keep_member_runner_inputs(self):
        packages = {"package.json": {"scripts": {"test": "root"}}, "web/package.json": {"scripts": {"test": "member"}}}
        for command in ("yarn workspace web test", "yarn workspaces foreach -A run test", "npm -wweb test"):
            with self.subTest(command=command):
                self.assertEqual({"root", "member"}, {body for _, _, body in self.roots(command, packages)})

    def test_product_loading_fields_merge_but_all_suite_policy_stays_on_base(self):
        before = {
            "name": "pkg",
            "scripts": {"test": "old"},
            "config": {"tests": "old"},
            "files": ["old"],
            "workspaces": ["old"],
            "jest": {"testMatch": ["old"]},
            "imports": {"#old": "./old.js"},
            "type": "commonjs",
            "main": "old.js",
            "module": "old.mjs",
        }
        after = {
            "name": "other",
            "scripts": {"test": "new", "pretest": "evil"},
            "config": {},
            "workspaces": [],
            "imports": {"#new": "./new.js"},
            "exports": {".": "./new.js"},
            "type": "module",
            "main": "new.js",
            "module": "new.mjs",
        }
        merged = json.loads(policy.merged_manifest(json.dumps(before), json.dumps(after)))
        self.assertEqual({**before, "imports": after["imports"], "exports": after["exports"]}, merged)

    def test_new_manifest_can_only_introduce_product_loading_fields(self):
        self.assertEqual('{"imports":{}}', policy.merged_manifest(None, '{"imports":{}}'))
        self.assertIsNone(policy.merged_manifest(None, '{"type":"module"}'))
        self.assertIsNone(policy.merged_manifest(None, '{"scripts":{"test":"true"}}'))
        self.assertEqual("{}", policy.merged_manifest("{}", "invalid"))
        self.assertEqual("{}", policy.merged_manifest("{}", "[]"))


if __name__ == "__main__":
    unittest.main()
