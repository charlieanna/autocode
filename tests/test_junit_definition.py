"""Conservative separation of old test definitions from mutable product source."""

import unittest

import autocode_junit_proof as proof


class JUnitDefinitionTests(unittest.TestCase):
    def definition(self, path, text, command):
        return proof.definition(
            ".",
            command,
            {path: ("100644", "blob")},
            lambda _: text,
            set(),
            {path: "modified"},
            is_test=lambda _: False,
        )

    def test_plain_java_product_is_separate_from_the_retained_definition(self):
        found = self.definition("src/main/java/PageCount.java", "class PageCount {}", "mvn test")
        self.assertFalse(found.boundary)
        self.assertFalse(found.changed)

    def test_nonstandard_test_bearing_source_cannot_be_mutable_product(self):
        for path, text, command in (
            ("checks/Checks.java", "@ org.junit.Test void guard() {}", "mvn test"),
            ("checks/Checks.java", "@/**/Test void guard() {}", "mvn test"),
            ("checks/Checks.kt", "import kotlin.test.Test as Guard; @Guard fun old() {}", "gradle test"),
            ("checks/Checks.cs", "[ NUnit.Framework.TestCase(1) ] void Guard() {}", "dotnet test"),
            ("checks/Checks.vb", "< Test > Public Sub Guard()", "dotnet test"),
            (
                "lib/widget.rb",
                'RSpec.describe Widget do\n  it "keeps old behavior" do\n  end\nend',
                "bundle exec rspec",
            ),
        ):
            with self.subTest(path=path):
                self.assertIn("possible nonstandard test", self.definition(path, text, command).boundary)

    def test_existing_rust_source_requires_an_established_inline_boundary(self):
        for text in ("pub fn f() {}", "#[tokio::test] fn guard() {}", "#[ test ] fn guard() {}"):
            self.assertIn(
                "possible nonstandard test", self.definition("src/lib.rs", text, "cargo nextest run").boundary
            )

    def test_unknown_changed_build_inputs_cannot_enter_the_definition_tree(self):
        found = self.definition("custom-selector.json", '{"include":"new-only"}', "mvn test")
        self.assertIn("outside the pinned maven definition", found.boundary)

    def test_unknown_commands_do_not_gain_a_build_boundary_from_xml_alone(self):
        self.assertIn("not established", self.definition("source.php", "<?php", "phpunit").boundary)


class JUnitCommandBoundaryTests(unittest.TestCase):
    def boundary(self, command, *, paths=()):
        return proof.command_boundary(command, proof.first.family(command), local_paths=paths)

    def test_documented_plain_commands_keep_their_definition_boundary(self):
        for command in (
            "mvn -o -B -q -Dtest=PageCountTest test",
            "mvn --define test=example.PageCountTest test",
            "mvn --define=test=PageCountTest test",
            "mvn -D test=PageCountTest test",
            "mvn -Dtest=PageCountTest -Dtest=PageCountTest test",
            "mvn -fae test",
            "mvn -ff test",
            "./gradlew test --tests example.PageCountTest --rerun-tasks",
            'dotnet test Tests.csproj --framework net8.0 --logger "junit;LogFilePath=junit.xml"',
            "dotnet test src/Checks.fsproj",
            "dotnet test ./Project.sln",
            "cargo nextest run --profile ci",
            "bundle exec rspec --format RspecJunitFormatter --out junit.xml",
        ):
            with self.subTest(command=command):
                self.assertEqual("", self.boundary(command))

    def test_file_selectors_are_unbound_in_separate_attached_equals_and_missing_forms(self):
        for runner, flags in (
            (
                "mvn",
                (
                    "-f",
                    "--file",
                    "-s",
                    "--settings",
                    "-gs",
                    "--global-settings",
                    "-t",
                    "--toolchains",
                    "-gt",
                    "--global-toolchains",
                ),
            ),
            (
                "gradle",
                (
                    "-b",
                    "--build-file",
                    "-c",
                    "--settings-file",
                    "-I",
                    "--init-script",
                    "-p",
                    "--project-dir",
                    "--include-build",
                ),
            ),
            ("dotnet test", ("-s", "--settings", "--project", "--solution", "--test-adapter-path")),
            ("cargo nextest run", ("--manifest-path", "--config-file", "--config", "--archive-file")),
            ("bundle exec rspec", ("-O", "--options", "-I", "--load-path")),
        ):
            for flag in flags:
                forms = (f"{flag} input.java", f"{flag}=input.java", flag, flag + "=", f"{flag} first {flag} second")
                if not flag.startswith("--"):
                    forms += (flag + "input.java",)
                for selected in forms:
                    with self.subTest(runner=runner, selected=selected):
                        self.assertTrue(self.boundary(f"{runner} {selected}"))

    def test_custom_properties_and_ambiguous_long_aliases_are_unbound(self):
        for command in (
            "mvn -Dselector=build.java test",
            "mvn -D selector=build.java test",
            "mvn --define=selector=build.java test",
            "mvn --define selector=build.java test",
            "mvn -Dmaven.ext.class.path=plugin.java test",
            "mvn -D test",
            "mvn --define",
            "mvn --def=selector=build.java test",
            "mvn --fi build.java test",
            "mvn -Dtest=../../outside.java test",
            "mvn -Dtest=One,Two test",
            "gradle -Pselector=configure.java test",
            "gradle -P selector=configure.java test",
            "gradle --project-prop=selector=configure.java test",
            "gradle -Dselector=configure.java test",
            "gradle --system-prop selector=configure.java test",
            "gradle --sys=selector=x test",
            "dotnet test -p:Selector=policy.cs",
            "dotnet test /p:Selector=policy.cs",
            "dotnet test --property Selector=policy.cs",
            "dotnet test --property=Selector=policy.cs",
            "dotnet test --property:Selector=policy.cs",
            "dotnet test -p",
            "dotnet test @policy.cs",
            "dotnet test -- RunConfiguration.Selector=policy.cs",
        ):
            with self.subTest(command=command):
                self.assertTrue(self.boundary(command))

    def test_dotnet_project_operands_must_have_a_contained_canonical_binding(self):
        for project in ("/tmp/Tests.csproj", "../Tests.csproj", "src/../Tests.csproj", "custom.cs", "custom"):
            with self.subTest(project=project):
                self.assertTrue(self.boundary("dotnet test " + project))

    def test_only_the_external_formatter_require_remains_supported(self):
        prefix = "bundle exec rspec "
        for selected in (
            "-r rspec_junit_formatter",
            "-rrspec_junit_formatter",
            "--require rspec_junit_formatter",
            "--require=rspec_junit_formatter",
        ):
            with self.subTest(selected=selected):
                self.assertEqual("", self.boundary(prefix + selected))
                self.assertTrue(self.boundary(prefix + selected, paths=["lib/rspec_junit_formatter.rb"]))
        for selected in (
            "-r ./helper",
            "-rlib/helper",
            "--require helper.rb",
            "--require=helper",
            "--require",
            "--require=",
            "--requ=local.rb",
            "-r rspec_junit_formatter -r local",
        ):
            with self.subTest(selected=selected):
                self.assertTrue(self.boundary(prefix + selected))

    def test_command_selected_product_named_files_cannot_enter_the_definition_overlay(self):
        for command, path, text in (
            (
                "mvn -f build.java test",
                "build.java",
                "<project><properties><guardExpected>2</guardExpected></properties></project>",
            ),
            ("gradle --init-script configure.java test", "configure.java", "allprojects { ext.guardExpected = 2 }"),
        ):
            with self.subTest(command=command):
                found = proof.definition(
                    ".",
                    command,
                    {path: ("100644", "blob")},
                    lambda _: text,
                    set(),
                    {path: "modified"},
                    is_test=lambda _: False,
                )
                self.assertIn("command-selected", found.boundary)

    def test_unbound_command_inputs_cannot_authorize_a_first_project_exception(self):
        listing = "100644 blob " + "a" * 40 + " 7\tapp.py\0"
        for command in (
            "mvn -f build.java test",
            "gradle --init-script configure.java test",
            "dotnet test --settings policy.cs",
            "cargo nextest run --config-file policy.rs",
            "bundle exec rspec -r local",
        ):
            with self.subTest(command=command):
                self.assertFalse(proof.no_project(command, listing, []))
        self.assertTrue(proof.no_project("mvn -Dtest=PageCountTest test", listing, []))


class JUnitTestSourceBoundaryTests(unittest.TestCase):
    def definition(self, path, text, command):
        return proof.definition(
            ".", command, {path: ("100644", "blob")}, lambda _: text, set(), {path: "modified"}, is_test=lambda _: False
        )

    def test_a_url_literal_cannot_hide_a_later_test_annotation(self):
        for path, source, command in (
            (
                "checks/Checks.java",
                'class Checks { String url="https://example"; @org.junit.jupiter.api.Test void old() {} }',
                "mvn test",
            ),
            (
                "checks/Checks.cs",
                'class Checks { string url="https://example"; [Xunit.Fact] void Old() {} }',
                "dotnet test",
            ),
        ):
            with self.subTest(path=path):
                self.assertIn("possible nonstandard test", self.definition(path, source, command).boundary)

    def test_unicode_identifiers_and_comment_separated_annotations_stay_unverified(self):
        for source in (
            r"class Checks { @org.j\u0075nit.jupiter.api.Te\u0073t void old() {} }",
            r'class Checks { String url="https://example"; @org.j\u0075nit.jupiter.api.Te\u0073t void old() {} }',
            r"class Checks { @org.j\uuuu0075nit.jupiter.api.Te\u0073t void old() {} }",
            "class Checks { @/**/Test void old() {} }",
        ):
            with self.subTest(source=source):
                self.assertIn(
                    "possible nonstandard test", self.definition("checks/Checks.java", source, "mvn test").boundary
                )

    def test_markers_from_other_ecosystems_do_not_turn_plain_product_into_tests(self):
        for path, source, command in (
            ("src/main/java/Widget.java", "class Widget { int test = 1; String context; }", "mvn test"),
            ("src/Widget.cs", "class Widget { string junit; void test() {} }", "dotnet test"),
            ("lib/widget.rb", 'class Widget; def fact; "xunit nunit"; end; end', "bundle exec rspec"),
        ):
            with self.subTest(path=path):
                self.assertEqual("", self.definition(path, source, command).boundary)


if __name__ == "__main__":
    unittest.main()
