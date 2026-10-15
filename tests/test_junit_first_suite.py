"""Pure controls: JUnit absence needs runner diagnostics plus pinned no-project policy."""

import copy
import unittest

import autocode_junit_first_suite as policy

COMMANDS = {
    "maven": "mvn -o -B -q -Dtest=PageCountTest test",
    "gradle": "./gradlew --no-daemon test --tests example.PageCountTest",
    "dotnet": 'dotnet test Tests.csproj --logger "junit;LogFilePath=junit.xml"',
    "nextest": "cargo nextest run --profile ci",
    "rspec": "bundle exec rspec --format RspecJunitFormatter --out junit.xml",
}
DIAGNOSTICS = {
    "maven": "[ERROR] The goal you specified requires a project to execute but there is no POM in this directory (/tmp/base). Please verify you invoked Maven from the correct directory. -> [Help 1]",
    "gradle": "* What went wrong:\nDirectory '/tmp/base' does not contain a Gradle build.\n",
    "dotnet": "MSBUILD : error MSB1003: Specify a project or solution file. The current working directory does not contain a project or solution file.",
    "nextest": "error: could not find `Cargo.toml` in `/tmp/base` or any parent directory\n",
    "rspec": "Could not locate Gemfile or .bundle/ directory\n",
}


def missing(family="maven"):
    return {
        "command": COMMANDS[family],
        "exit_code": 1,
        "timed_out": False,
        "results": None,
        "results_format": "junit",
        "report_status": "missing",
        "tail": DIAGNOSTICS[family],
    }


def passing(family="maven"):
    return {
        "command": COMMANDS[family],
        "exit_code": 0,
        "timed_out": False,
        "results_format": "junit",
        "report_status": "captured",
        "results": {
            "complete": True,
            "total": 1,
            "passed": ["Example::test_c1"],
            "failed": [],
            "skipped": [],
            "collection_errors": [],
            "uncollected": [],
        },
    }


class CommandFamilyTests(unittest.TestCase):
    def test_plain_runners_and_quoted_dotnet_logger_have_exact_families(self):
        for family, command in COMMANDS.items():
            with self.subTest(command=command):
                self.assertEqual(family, policy.family(command))
        for command, family in (
            ("/opt/maven/bin/mvn test", "maven"),
            ("./mvnw test", "maven"),
            ("gradle test", "gradle"),
            ("rspec spec/unit_spec.rb", "rspec"),
        ):
            with self.subTest(command=command):
                self.assertEqual(family, policy.family(command))

    def test_shell_programs_wrappers_and_other_subcommands_are_unknown(self):
        for command in (
            None,
            "",
            "mvn test && true",
            "mvn test; echo ok",
            "mvn test | tee out",
            "mvn test > out",
            "mvn test # old command",
            "(mvn test)",
            "mvn test\ntrue",
            "mvn $(echo test)",
            "mvn `echo test`",
            "env FOO=x mvn test",
            "FOO=x mvn test",
            "sh -c 'mvn test'",
            "command mvn test",
            "dotnet build",
            "cargo test",
            "cargo nextest list",
            "bundle install",
            "mvn 'unterminated",
            "./run-tests.sh",
            "npm test",
        ):
            with self.subTest(command=command):
                self.assertIsNone(policy.family(command))


class InventoryTests(unittest.TestCase):
    def test_other_language_base_does_not_invent_a_selected_project(self):
        for family, paths in (
            ("maven", ["README.md", "legacy/app.cs"]),
            ("gradle", ["legacy/main.rs"]),
            ("dotnet", ["main.java", "pom.xml"]),
            ("nextest", ["app.cs", "build.gradle"]),
            ("rspec", ["app.java", "Cargo.toml"]),
        ):
            with self.subTest(family=family):
                self.assertFalse(policy.contains_project(family, paths))
                self.assertFalse(policy.contains_project(family, []))

    def test_existing_source_or_artifacts_prevent_no_project_claim(self):
        for family, paths in {
            "maven": ["module/src/App.java", "legacy/Helper.kt", "lib/app.jar"],
            "gradle": ["legacy/build.scala", "legacy/app.groovy", "lib/App.class"],
            "dotnet": ["legacy/Program.cs", "lib/old.dll", "src/library.fs"],
            "nextest": ["legacy/main.rs", "lib/app.rlib"],
            "rspec": ["lib/app.rb"],
        }.items():
            for path in paths:
                with self.subTest(family=family, path=path):
                    self.assertTrue(policy.contains_project(family, [path]))

    def test_framework_definitions_are_pinned_and_also_count_as_project_markers(self):
        definitions = {
            "maven": ["module/pom.xml", ".mvn/maven.config", ".mvn/extensions.xml", "mvnw", "mvnw.cmd", "settings.xml"],
            "gradle": [
                "build.gradle",
                "build.gradle.kts",
                "settings.gradle.kts",
                "gradle.properties",
                "gradle/wrapper/gradle-wrapper.properties",
                "gradle/libs.versions.toml",
                "gradlew.bat",
                "buildSrc/src/main/kotlin/ConventionPlugin.kt",
            ],
            "nextest": [
                "Cargo.toml",
                "nested/Cargo.lock",
                ".cargo/config.toml",
                ".config/nextest.toml",
                "build.rs",
                "rust-toolchain.toml",
            ],
            "dotnet": [
                "src/Tests.csproj",
                "app.fsproj",
                "app.vbproj",
                "Project.sln",
                "Project.slnx",
                "Directory.Build.props",
                "Directory.Build.targets",
                "Directory.Packages.props",
                "test.runsettings",
                "NuGet.config",
                "global.json",
                "packages.config",
            ],
            "rspec": [
                "Gemfile",
                "Gemfile.lock",
                ".rspec",
                "Rakefile",
                "app.gemspec",
                ".bundle/config",
                "spec/spec_helper.rb",
                "spec/support/factory.rb",
                "spec/unit/support/nested.rb",
            ],
        }
        for family, paths in definitions.items():
            for path in paths:
                with self.subTest(family=family, path=path):
                    self.assertTrue(policy.definition_file(path, family))
                    self.assertTrue(policy.contains_project(family, [path]))

    def test_product_sources_are_not_automatically_test_definition_inputs(self):
        for family, path in (
            ("maven", "src/main/java/App.java"),
            ("gradle", "src/main/kotlin/App.kt"),
            ("dotnet", "src/App.cs"),
            ("nextest", "src/lib.rs"),
            ("rspec", "lib/app.rb"),
        ):
            with self.subTest(family=family):
                self.assertFalse(policy.definition_file(path, family))

    def test_unknown_or_unbounded_inventory_cannot_prove_absence(self):
        for family, paths in (
            (None, []),
            ("unknown", []),
            ("maven", None),
            ("maven", "pom.xml"),
            ("maven", [None]),
            ("maven", ["../pom.xml"]),
            ("maven", ["/tmp/pom.xml"]),
            ("maven", ["broken\x00path"]),
        ):
            with self.subTest(family=family, paths=paths):
                self.assertTrue(policy.contains_project(family, paths))
        self.assertTrue(policy.definition_file("some/unknown", None))


class AbsenceTests(unittest.TestCase):
    def test_each_known_no_project_diagnostic_is_an_absence_observation(self):
        for family in COMMANDS:
            with self.subTest(family=family):
                self.assertTrue(policy.reported_absence(missing(family), family))
                self.assertTrue(policy.absent_suite(missing(family), passing(family), family=family, no_project=True))
        colored = missing()
        colored["tail"] = "\x1b[31m" + colored["tail"] + "\x1b[0m"
        self.assertTrue(policy.reported_absence(colored, "maven"))

    def test_missing_xml_or_unrelated_failure_is_not_no_project_evidence(self):
        for family in COMMANDS:
            for text in ("JUnit report was not written", "BUILD FAILURE", "No tests found", "compilation failed", ""):
                with self.subTest(family=family, text=text):
                    self.assertFalse(policy.reported_absence({**missing(family), "tail": text}, family))
        for family, text in (
            ("maven", "Non-resolvable parent POM"),
            (
                "gradle",
                "Project directory '/tmp/x' is not part of the build defined by settings file '/tmp/settings.gradle'",
            ),
            ("dotnet", "MSBUILD : error MSB1009: Project file does not exist."),
            ("nextest", "error: failed to parse manifest at Cargo.toml"),
            ("rspec", "LoadError: cannot load such file -- spec_helper"),
        ):
            with self.subTest(family=family, text=text):
                self.assertFalse(policy.reported_absence({**missing(family), "tail": text}, family))

    def test_no_project_diagnostic_must_belong_to_the_recorded_command(self):
        for family in COMMANDS:
            receipt = missing(family)
            for other in set(COMMANDS) - {family}:
                with self.subTest(family=family, command_family=other):
                    self.assertFalse(policy.reported_absence({**receipt, "command": COMMANDS[other]}, family))
            self.assertFalse(policy.reported_absence({**receipt, "command": COMMANDS[family] + " && true"}, family))

    def test_nonordinary_failed_missing_or_contradictory_receipts_are_not_absence(self):
        changes = [{"exit_code": code} for code in (0, None, -9, True, "1")]
        changes += [
            {"timed_out": True},
            {"timed_out": None},
            {"error": "keeper failed"},
            {"interrupted": True},
            {"cancelled": True},
            {"results_format": "pytest"},
            {"report_status": "malformed"},
            {"report_status": "captured"},
            {"results": {}},
            {"test_report": {"sha256": "old"}},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(policy.reported_absence({**missing(), **change}, "maven"))
        self.assertFalse(policy.reported_absence(None, "maven"))
        self.assertFalse(policy.reported_absence(missing(), None))

    def test_pinned_inventory_absence_is_required_even_for_honest_diagnostics(self):
        for no_project in (False, None, 1, "yes"):
            with self.subTest(no_project=no_project):
                self.assertFalse(policy.absent_suite(missing(), passing(), family="maven", no_project=no_project))

    def test_candidate_must_execute_complete_positive_cases_without_failures_or_skips(self):
        changes = [{"exit_code": code} for code in (None, 1, -9, True)]
        changes += [
            {"timed_out": True},
            {"error": "cleanup failed"},
            {"results_format": "unknown"},
            {"command": COMMANDS["gradle"]},
            {"results": None},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(
                    policy.absent_suite(missing(), {**passing(), **change}, family="maven", no_project=True)
                )
        for field, value in (
            ("complete", False),
            ("complete", 1),
            ("total", 0),
            ("total", 2),
            ("total", True),
            ("passed", []),
            ("passed", ["same", "same"]),
            ("passed", [""]),
            ("failed", ["other"]),
            ("skipped", ["other"]),
            ("collection_errors", ["other"]),
            ("uncollected", ["other"]),
            ("skipped", None),
        ):
            candidate = passing()
            candidate["results"][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(policy.absent_suite(missing(), candidate, family="maven", no_project=True))

    def test_policy_does_not_modify_saved_receipts(self):
        base, candidate = missing(), passing()
        before = copy.deepcopy((base, candidate))
        self.assertTrue(policy.absent_suite(base, candidate, family="maven", no_project=True))
        self.assertEqual(before, (base, candidate))


if __name__ == "__main__":
    unittest.main()
