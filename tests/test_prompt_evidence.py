"""Prompt edits require scoped PR disclosures without executing untrusted code."""

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import check_prompt_evidence as gate

PROMPT = "tools/prompts/builder.md"
OTHER = "tools/prompts/fragments/tester/report.md"


def live(resources=PROMPT):
    return f"""## Live run evidence
### Candidate run
- Resources: `{resources}`
- Command: `autocode --provider kilocode task`
- Provider: kilocode
- Models: Builder=zai/glm-5.3; Tester=zai/glm-5.3
- Verdict: EXPECTED_DECISION_PAUSE
- Duration: 1.25 seconds
- Run directory: live-candidate-01
- Reach: Builder loaded the declared changed resource; its hash is in the attempt.
"""


def exempt(resources=PROMPT, reason="Removed an unreachable prompt; fault-injected removal control covers this path"):
    return f"## Live run evidence\n- Resources: `{resources}`\n- Exempt reason: {reason}\n"


def metadata(body="", count=1):
    return {
        "number": 7,
        "body": body,
        "changed_files": count,
        "head": {"sha": "a" * 40},
        "base": {"sha": "b" * 40, "repo": {"full_name": "owner/repo"}},
    }


class DisclosureTests(unittest.TestCase):
    def test_a_real_run_discloses_all_scoped_resources(self):
        body = live(PROMPT + "`, `" + OTHER)
        result = gate.disclosure(body, [PROMPT, OTHER])
        self.assertFalse(result.errors)
        self.assertEqual({PROMPT, OTHER}, set(result.covered))

    def test_an_exemption_is_scoped_to_the_named_file(self):
        result = gate.disclosure(exempt(), [PROMPT, OTHER])
        self.assertEqual({PROMPT}, set(result.covered))
        self.assertTrue(result.errors)
        self.assertIn(OTHER, " ".join(result.errors))

    def test_empty_unrelated_and_outside_section_evidence_does_not_pass(self):
        for body in (
            "",
            live(OTHER),
            live().replace("Live run evidence", "How I tested it"),
            "## Live run evidence\n\n## Other section\n" + live().split("\n", 1)[1],
        ):
            with self.subTest(body=body):
                self.assertTrue(gate.disclosure(body, [PROMPT]).errors)

    def test_a_file_list_elsewhere_does_not_borrow_a_live_run(self):
        body = f"## Changed resources\n- `{PROMPT}`\n" + live(OTHER)
        self.assertTrue(gate.disclosure(body, [PROMPT]).errors)

    def test_every_qualification_field_is_required(self):
        for label in ("Command", "Provider", "Models", "Verdict", "Duration", "Run directory", "Reach"):
            with self.subTest(label=label):
                body = "\n".join(line for line in live().splitlines() if not line.startswith("- " + label + ":"))
                self.assertTrue(gate.disclosure(body, [PROMPT]).errors)

    def test_placeholder_fields_and_empty_exempt_reasons_fail(self):
        for value in ("", "TODO run it", "TBD", "pending", "N/A", "not run"):
            with self.subTest(value=value):
                self.assertTrue(gate.disclosure(live().replace("kilocode\n", value + "\n"), [PROMPT]).errors)
                self.assertTrue(gate.disclosure(exempt(reason=value), [PROMPT]).errors)

    def test_fake_only_and_invalid_durations_need_a_scoped_exemption(self):
        for body in (
            live().replace("Provider: kilocode", "Provider: fake-provider"),
            live().replace("autocode --provider", "autocode --fake --provider"),
            live().replace("1.25 seconds", "0 seconds"),
            live().replace("1.25 seconds", "fast"),
        ):
            with self.subTest(body=body):
                self.assertTrue(gate.disclosure(body, [PROMPT]).errors)

    def test_code_examples_comments_and_quotes_are_not_a_designated_section(self):
        for body in (
            "```md\n" + live() + "```\n",
            "<!--\n" + live() + "-->\n",
            "<!--\n" + live(),
            "> " + live().replace("\n", "\n> "),
            "\n".join("    " + x for x in live().splitlines()),
        ):
            with self.subTest(body=body):
                self.assertTrue(gate.disclosure(body, [PROMPT]).errors)

    def test_an_unrelated_block_cannot_supply_missing_fields(self):
        body = exempt(reason="TODO") + "### Unrelated\n" + live(OTHER).split("### Candidate run\n", 1)[1]
        self.assertTrue(gate.disclosure(body, [PROMPT]).errors)

    def test_duplicate_sections_or_fields_fail(self):
        for body in (live() + live(), live() + "- Provider: other\n"):
            with self.subTest(body=body):
                self.assertTrue(gate.disclosure(body, [PROMPT]).errors)

    def test_no_prompt_changes_need_no_live_claim(self):
        self.assertFalse(gate.disclosure("", []).errors)

    def test_summary_lists_exact_resources_safely_and_disclaims_attestation(self):
        path = "tools/prompts/<tag>|newline\n.md"
        text = gate.summary(gate.disclosure("", [path]))
        self.assertIn("&lt;tag&gt;&#124;newline&#10;.md", text)
        self.assertIn("does not attest execution", text)
        self.assertNotIn("<tag>", text)


class InventoryTests(unittest.TestCase):
    def test_deletion_and_both_sides_of_rename_are_inventory_members(self):
        files = [
            {"status": "removed", "filename": PROMPT},
            {"status": "renamed", "filename": OTHER, "previous_filename": "tools/prompts/old.md"},
            {"status": "renamed", "filename": "docs/old.md", "previous_filename": "tools/prompts/moved-out.md"},
            {"status": "added", "filename": "docs/guide.md"},
        ]
        self.assertEqual(
            (PROMPT, OTHER, "tools/prompts/moved-out.md", "tools/prompts/old.md"), gate.changed_resources(files)
        )

    def test_missing_rename_source_and_unknown_status_fail_closed(self):
        for row in ({"status": "renamed", "filename": PROMPT}, {"status": "mystery", "filename": PROMPT}):
            with self.subTest(row=row):
                self.assertRaises(gate.GateError, gate.changed_resources, [row])

    def test_pagination_and_current_body_are_read_only_and_complete(self):
        files = [{"status": "modified", "filename": f"docs/{i}.md"} for i in range(100)] + [
            {"status": "added", "filename": PROMPT}
        ]
        paths = []

        def get(path):
            paths.append(path)
            if "/files?" not in path:
                return metadata(live(), len(files))
            return files[:100] if path.endswith("page=1") else files[100:]

        body, resources = gate.pull_request("owner/repo", 7, get)
        self.assertEqual(live(), body)
        self.assertEqual((PROMPT,), resources)
        self.assertEqual(
            [
                "/repos/owner/repo/pulls/7",
                "/repos/owner/repo/pulls/7/files?per_page=100&page=1",
                "/repos/owner/repo/pulls/7/files?per_page=100&page=2",
                "/repos/owner/repo/pulls/7",
            ],
            paths,
        )

    def test_incomplete_duplicate_or_malformed_file_pages_fail_closed(self):
        file = {"status": "modified", "filename": PROMPT}
        for count, page in ((2, [file]), (2, [file, file]), (1, {"message": "error"}), (1, [{"filename": PROMPT}])):
            with self.subTest(count=count, page=page):

                def get(path):
                    return page if "/files?" in path else metadata(count=count)

                self.assertRaises(gate.GateError, gate.pull_request, "owner/repo", 7, get)

    def test_capped_or_invalid_metadata_fails_before_files_are_read(self):
        for value in (metadata(count=3000), metadata(count=True), {"number": 7}, metadata(count=-1)):
            with self.subTest(value=value):
                get = mock.Mock(return_value=value)
                self.assertRaises(gate.GateError, gate.pull_request, "owner/repo", 7, get)
                self.assertEqual(1, get.call_count)

    def test_head_base_body_or_file_count_change_during_inventory_fails(self):
        first = metadata(live())
        changes = [metadata(exempt()), metadata(live(), 2)]
        for key in ("head", "base"):
            value = copy.deepcopy(first)
            value[key]["sha"] = "c" * 40
            changes.append(value)
        for changed in changes:
            with self.subTest(changed=changed):
                get = mock.Mock(side_effect=[first, [{"status": "modified", "filename": PROMPT}], changed])
                self.assertRaises(gate.GateError, gate.pull_request, "owner/repo", 7, get)

    def test_a_fork_head_is_accepted_but_a_foreign_base_is_not(self):
        value = metadata(live())
        value["head"]["repo"] = {"full_name": "fork/repo"}
        get = mock.Mock(side_effect=[value, [{"status": "modified", "filename": PROMPT}], value])
        self.assertEqual((PROMPT,), gate.pull_request("owner/repo", 7, get)[1])
        value["base"]["repo"]["full_name"] = "foreign/repo"
        self.assertRaises(gate.GateError, gate.pull_request, "owner/repo", 7, mock.Mock(return_value=value))


class TransportAndWorkflowTests(unittest.TestCase):
    def test_transport_only_gets_fixed_github_origin_with_a_bounded_read(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.getcode.return_value = 200
        response.read.return_value = b'{"number": 7}'
        with mock.patch.object(gate, "build_opener") as build:
            build.return_value.open.return_value = response
            client = gate.GitHub("TEST_TOKEN")
            self.assertEqual({"number": 7}, client.get("/repos/owner/repo/pulls/7"))
            request = build.return_value.open.call_args.args[0]
            self.assertEqual("GET", request.get_method())
            self.assertEqual("https://api.github.com/repos/owner/repo/pulls/7", request.full_url)
            self.assertEqual(20, build.return_value.open.call_args.kwargs["timeout"])
            response.read.assert_called_once_with(gate.MAX_BYTES + 1)

    def test_redirects_api_errors_oversize_and_invalid_json_fail_closed(self):
        self.assertRaises(
            gate.GateError, gate.NoRedirect().redirect_request, None, None, 302, "", {}, "https://evil.example"
        )
        for data in (b"not json", b"\xff", b"x" * (gate.MAX_BYTES + 1)):
            with self.subTest(size=len(data)):
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.getcode.return_value = 200
                response.read.return_value = data
                with mock.patch.object(gate, "build_opener") as build:
                    build.return_value.open.return_value = response
                    self.assertRaises(gate.GateError, gate.GitHub("TEST").get, "/repos/owner/repo/pulls/7")
        with mock.patch.object(gate, "build_opener") as build:
            build.return_value.open.side_effect = gate.HTTPError("https://api.github.com", 403, "forbidden", {}, None)
            self.assertRaises(gate.GateError, gate.GitHub("TEST").get, "/repos/owner/repo/pulls/7")

    def test_failed_disclosure_still_writes_changed_file_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p / "event.json").write_text(json.dumps({"pull_request": {"number": 7}}))
            env = {
                "GITHUB_EVENT_PATH": str(p / "event.json"),
                "GITHUB_REPOSITORY": "owner/repo",
                "GITHUB_TOKEN": "TEST",
                "GITHUB_STEP_SUMMARY": str(p / "summary.md"),
            }
            with (
                mock.patch.object(gate, "pull_request", return_value=("", (PROMPT,))),
                mock.patch.object(gate, "GitHub"),
                mock.patch("sys.stdout", new_callable=io.StringIO),
            ):
                self.assertEqual(1, gate.main(env))
            self.assertIn(PROMPT, (p / "summary.md").read_text())

    def test_workflow_uses_trusted_base_and_retriggers_on_body_edits(self):
        text = (Path(__file__).resolve().parents[1] / ".github/workflows/prompt-evidence.yml").read_text()
        self.assertIn("pull_request_target:", text)
        self.assertIn("edited", text)
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", text)
        self.assertIn("repository: ${{ github.repository }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("contents: read", text)
        self.assertIn("pull-requests: read", text)
        self.assertNotIn("head.sha", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("pip install", text)


if __name__ == "__main__":
    unittest.main()
