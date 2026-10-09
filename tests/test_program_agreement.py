"""Program agreement rules: validation, inheritance, scope fingerprints, revisions, interface guards, rendering.

Pure tests over autocode_program_agreement. Manifests go through
autocode_program.validate_manifest first, so they are normalized the way the
program controller sees them.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import autocode_program as program  # noqa: E402
import autocode_program_agreement as agreement  # noqa: E402


def raw():
    """A fresh, valid manifest. ``web`` neither produces nor consumes ``api``; ``style`` has no producer."""
    return {
        "version": 1, "name": "Demo", "brief": "Demo outcome",
        "requirements": [{"id": "C1", "criterion": "a student can practice a lesson"},
                         {"id": "C2", "criterion": "the engine picks the next activity"}],
        "journeys": [{"id": "J1", "name": "Student practice",
                      "steps": ["read lesson", "answer with hint", "see next activity"]}],
        "shared": {"constraints": ["stdlib only"],
                   "interfaces": [{"id": "api", "summary": "next activity JSON", "paths": ["skeleton/api"],
                                   "producer": "skeleton", "consumers": ["engine"]},
                                  {"id": "style", "summary": "shared look", "paths": ["web/style"]}]},
        "workstreams": [
            {"id": "skeleton", "kind": "code", "skeleton": True, "brief": "thin journey", "owns": ["skeleton"],
             "depends_on": [], "acceptance_criteria": ["C1"]},
            {"id": "engine", "kind": "code", "brief": "engine", "owns": ["engine"], "depends_on": ["skeleton"],
             "acceptance_criteria": ["C2"]},
            {"id": "web", "kind": "code", "brief": "web", "owns": ["web"], "depends_on": ["skeleton"]},
            {"id": "integration", "kind": "integration", "brief": "final check", "owns": [],
             "depends_on": ["engine", "web"]},
        ],
    }


def ws(manifest, wid):
    return next(row for row in manifest["workstreams"] if row["id"] == wid)


def iface(manifest, iid):
    return next(row for row in manifest["shared"]["interfaces"] if row["id"] == iid)


def build(change=None):
    value = raw()
    if change:
        change(value)
    return program.validate_manifest(value)


def add_workstream(manifest, row):
    """Add a code workstream before integration and make integration depend on it."""
    manifest["workstreams"].insert(-1, {"kind": "code", "brief": row["id"], "owns": [row["id"]], **row})
    ws(manifest, "integration")["depends_on"].append(row["id"])


class ValidateTest(unittest.TestCase):
    def rejects(self, cases):
        for label, change, message in cases:
            with self.subTest(label):
                with self.assertRaisesRegex(ValueError, message):
                    build(change)

    def test_base_manifest_is_valid(self):
        manifest = build()
        self.assertEqual(agreement.skeleton(manifest), "skeleton")
        self.assertEqual(iface(manifest, "api")["version"], 1)  # defaulted
        self.assertEqual(iface(manifest, "style")["version"], 1)

    def test_journeys_are_required_and_well_formed(self):
        def journey(**fields):
            return lambda m: m["journeys"][0].update(fields)

        self.rejects([
            ("missing", lambda m: m.pop("journeys"), "needs journeys"),
            ("empty", lambda m: m.update(journeys=[]), "needs journeys"),
            ("bad id", journey(id="J 1"), "journey needs an id"),
            ("missing name", lambda m: m["journeys"][0].pop("name"), "Journey J1 needs a name"),
            ("blank name", journey(name="  "), "Journey J1 needs a name"),
            ("empty steps", journey(steps=[]), r"journey J1\.steps must be a nonempty list"),
            ("unknown field", journey(owner="web"), "Journey fields must be within"),
            ("simulated without does_not_prove", journey(simulated=True), "does_not_prove"),
            ("simulated blank does_not_prove", journey(simulated=True, does_not_prove=" "), "does_not_prove"),
            ("duplicate ids", lambda m: m["journeys"].append(dict(m["journeys"][0])), "Journey ids must be unique"),
            ("same id as a requirement", journey(id="C1"), "Journey ids and requirement ids must differ"),
        ])
        manifest = build(journey(simulated=True, does_not_prove="a real payment provider"))
        self.assertTrue(manifest["journeys"][0]["simulated"])

    def test_exactly_one_code_skeleton_without_dependencies(self):
        def move_to(wid):
            def change(m):
                ws(m, "skeleton").pop("skeleton")
                ws(m, wid)["skeleton"] = True
            return change

        self.rejects([
            ("none", lambda m: ws(m, "skeleton").pop("skeleton"), "exactly one"),
            ("two", lambda m: ws(m, "web").update(skeleton=True), "exactly one"),
            ("not a boolean", lambda m: ws(m, "skeleton").update(skeleton="yes"), "skeleton must be true or false"),
            ("has dependencies", move_to("engine"), "must be a code workstream with no dependencies"),
            ("not code", move_to("integration"), "must be a code workstream with no dependencies"),
        ])

    def test_code_workstreams_extend_the_skeleton_unless_exempt(self):
        unrooted = {"id": "tools", "depends_on": []}
        self.rejects([
            ("no path to the skeleton", lambda m: add_workstream(m, unrooted), "must \\(transitively\\) depend on "
                                                                               "the walking skeleton skeleton"),
            ("blank reason", lambda m: add_workstream(m, {**unrooted, "skeleton_exempt": " "}),
             "skeleton_exempt must give the reason"),
            ("exempt integration", lambda m: ws(m, "integration").update(skeleton_exempt="checks only"),
              "only a content workstream may be skeleton_exempt"),
            ("exempt skeleton", lambda m: ws(m, "skeleton").update(skeleton_exempt="it is the skeleton"),
              "only a content workstream may be skeleton_exempt"),
        ])
        exempt = build(lambda m: add_workstream(m, {**unrooted, "kind": "content", "skeleton_exempt": "lesson text only"}))
        self.assertFalse(agreement.needs_skeleton(exempt, "tools"))
        transitive = build(lambda m: add_workstream(m, {"id": "ui", "depends_on": ["engine"]}))
        self.assertTrue(agreement.needs_skeleton(transitive, "ui"))

    def test_interfaces_are_versioned_and_bound_to_the_graph(self):
        def api(**fields):
            return lambda m: iface(m, "api").update(fields)

        self.rejects([
            ("version zero", api(version=0), "version must be a positive integer"),
            ("negative version", api(version=-1), "version must be a positive integer"),
            ("boolean version", api(version=True), "version must be a positive integer"),
            ("string version", api(version="2"), "version must be a positive integer"),
            ("unknown producer", api(producer="nobody"), "producer 'nobody' is not a workstream"),
            ("unknown consumer", api(consumers=["nobody"]), "consumers must be distinct workstream ids"),
            ("duplicate consumer", api(consumers=["engine", "engine"]), "consumers must be distinct workstream ids"),
            ("consumers without producer", lambda m: iface(m, "api").pop("producer"), "consumers need a producer"),
            ("producer consumes", api(consumers=["engine", "skeleton"]), "producer cannot also be a consumer"),
            ("consumer does not depend on producer", api(producer="engine", consumers=["web"], paths=["engine/api"]),
             "consumer web must \\(transitively\\) depend on its producer engine"),
            ("paths outside producer", api(paths=["skeleton/api", "web/api"]), "producer skeleton does not own web/api"),
            ("unknown key", api(consumer="engine"), "Interface fields must be within"),
            ("duplicate id", lambda m: m["shared"]["interfaces"].append({"id": "api", "summary": "again"}),
             "Interface ids must be unique"),
        ])
        # validate_manifest refuses these shapes before the agreement rules run; the rules refuse them too.
        for label, change, message in [
                ("list producer", {"producer": ["skeleton"]}, r"producer \['skeleton'\] is not a workstream"),
                ("list consumer", {"consumers": [["engine"]]}, "consumers must be distinct workstream ids")]:
            with self.subTest(label):
                manifest = build()
                iface(manifest, "api").update(change)
                with self.assertRaisesRegex(ValueError, message):
                    agreement.validate(manifest)
        self.assertEqual(iface(build(api(version=3)), "api")["version"], 3)

    def test_requirements_are_well_formed_and_all_assigned(self):
        def requirement(**fields):
            return lambda m: m["requirements"][0].update(fields)

        contract = {"body": {"acceptance_criteria": [{"id": "C1", "criterion": "x"}, {"id": "C2", "criterion": "y"}]}}
        self.rejects([
            ("not a list", lambda m: m.update(requirements={"C1": "x"}), "requirements must be a list"),
            ("missing criterion", lambda m: m["requirements"][0].pop("criterion"), "need an id and a criterion"),
            ("blank criterion", requirement(criterion=" "), "need an id and a criterion"),
            ("bad id", requirement(id="C 1"), "need an id and a criterion"),
            ("unknown field", requirement(owner="web"), "need an id and a criterion"),
            ("duplicate ids", requirement(id="C2"), "Requirement ids must be unique"),
            ("with a contract", lambda m: m.update(contract=contract), "not both"),
            ("unknown id on a workstream", lambda m: ws(m, "web").update(acceptance_criteria=["C9"]),
             "Workstream web lists 'C9', which is not a requirement"),
            ("unassigned requirement", lambda m: ws(m, "engine").pop("acceptance_criteria"),
             "C2 are assigned to no workstream"),
        ])

    def test_requirements_may_come_from_the_parent_contract(self):
        def use_contract(m):
            m["contract"] = {"body": {"acceptance_criteria": m.pop("requirements")}}

        manifest = build(use_contract)
        self.assertEqual(list(agreement.requirements(manifest)), ["C1", "C2"])
        self.assertEqual(agreement.inherited(manifest, "engine"), ["C2"])

        def criterion(change):
            return lambda m: (use_contract(m), change(m["contract"]["body"]["acceptance_criteria"]))

        self.rejects([
            ("duplicate ids", criterion(lambda rows: rows[0].update(id="C2")), "Requirement ids must be unique"),
            ("missing criterion", criterion(lambda rows: rows[0].pop("criterion")), "need an id and a criterion"),
            ("blank criterion", criterion(lambda rows: rows[0].update(criterion=" ")), "need an id and a criterion"),
            ("missing id", criterion(lambda rows: rows[0].pop("id")), "need an id and a criterion"),
        ])

    def test_parent_boundaries_are_nonempty_string_lists_before_launch(self):
        for key in ("constraints", "permission_boundaries", "scope_exclusions"):
            for invalid in (None, "No network", [{"text": "No network"}], [""], ["  "], [1]):
                def change(value):
                    value["contract"] = {"body": {"acceptance_criteria": value.pop("requirements"), key: invalid}}
                with self.subTest(key=key, invalid=invalid), self.assertRaisesRegex(ValueError, key):
                    build(change)

    def test_skeleton_binds_only_its_selected_named_journeys(self):
        value = build(lambda m: (m["journeys"].append({"id": "J2", "name": "Extended", "steps": ["extended flow"]}),
                                 ws(m, "skeleton").update(journeys=["J1"])))
        self.assertEqual(["C1", "J1"], agreement.inherited(value, "skeleton"))
        self.assertEqual(["C1", "C2", "J1", "J2"], agreement.inherited(value, "integration"))
        new = copy.deepcopy(value)
        new["journeys"][1]["steps"] = ["changed extension"]
        self.assertEqual(["integration"], agreement.affected(value, new))
        for selected in ([], ["unknown"], ["J1", "J1"]):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                build(lambda m: ws(m, "skeleton").update(journeys=selected))

    def test_exempt_content_cannot_consume_a_runtime_interface(self):
        with self.assertRaisesRegex(ValueError, "cannot produce or consume a runtime interface"):
            build(lambda m: (ws(m, "web").update(kind="content", skeleton_exempt="Lesson text"),
                             iface(m, "api").update(consumers=["engine", "web"])))

    def test_inherited_literals_cannot_change_case_punctuation_or_inner_whitespace(self):
        value = build(lambda m: m["requirements"][1].update(criterion="Use the token 'AbC  .'"))
        expected = agreement.definitions(value, "engine")["C2"]
        self.assertEqual([], agreement.dropped(value, "engine", [{**expected, "criterion": "  " + expected["criterion"] + "\n"}]))
        for text in ("Use the token 'abc  .'", "Use the token 'AbC .'", "Use the token 'AbC  '"):
            with self.subTest(text=text):
                self.assertEqual(["C2"], agreement.dropped(value, "engine", [{**expected, "criterion": text}]))
        value["shared"]["permission_boundaries"] = ["No calls to /PrivateAPI"]
        self.assertEqual(["permission_boundaries: No calls to /PrivateAPI"],
                         agreement.lost_boundaries(value, {"constraints": value["shared"]["constraints"],
                                                          "permission_boundaries": ["No calls to /privateapi"]}))

    def test_checks_are_lists_of_commands(self):
        self.rejects([
            ("program checks", lambda m: m.update(checks=["make test", ""]), "checks must be a list"),
            ("workstream checks", lambda m: ws(m, "web").update(checks="make test"), r"web\.checks must be a list"),
        ])


class InheritanceTest(unittest.TestCase):
    def test_only_explicit_true_review_obligations_are_protected(self):
        for flag in (False, True, None):
            value = build()
            if flag is not None:
                value["requirements"][0]["human_review"] = flag
            definitions = agreement.definitions(value, "skeleton")
            rows = list(definitions.values())
            self.assertNotIn("human_review", definitions["J1"])
            self.assertEqual([], agreement.dropped(value, "skeleton", rows))
            for replacement in (False, None):
                with self.subTest(flag=flag, replacement=replacement):
                    revised = copy.deepcopy(rows)
                    criterion = next(row for row in revised if row["id"] == "C1")
                    if replacement is None:
                        criterion.pop("human_review", None)
                    else:
                        criterion["human_review"] = replacement
                    expected = ["C1"] if flag is True else []
                    self.assertEqual(expected, agreement.dropped(value, "skeleton", revised))

    def test_workstreams_inherit_their_requirements_and_integration_inherits_everything(self):
        manifest = build()
        self.assertEqual(agreement.inherited(manifest, "skeleton"), ["C1", "J1"])
        self.assertEqual(agreement.inherited(manifest, "engine"), ["C2"])
        self.assertEqual(agreement.inherited(manifest, "web"), [])
        self.assertEqual(agreement.inherited(manifest, "integration"), ["C1", "C2", "J1"])

    def test_requirements_only_deployment_owns_are_not_the_final_checks(self):
        def deploy(m, criterion="the site is live"):
            m["requirements"].append({"id": "C3", "criterion": criterion})
            m["workstreams"].append({"id": "deploy", "kind": "deployment", "brief": "publish", "owns": ["ops"],
                                     "depends_on": ["integration"], "acceptance_criteria": ["C3"]})

        manifest = build(deploy)
        self.assertEqual(agreement.inherited(manifest, "integration"), ["C1", "C2", "J1"])
        self.assertEqual(agreement.inherited(manifest, "deploy"), ["C3"])
        self.assertEqual(agreement.dropped(manifest, "integration", list(agreement.definitions(manifest, "integration").values())), [])
        revised = build(lambda m: deploy(m, "the site is live over HTTPS"))
        self.assertEqual(agreement.affected(manifest, revised), ["deploy"])
        # Assigned to a code workstream as well, the final check keeps it.
        shared = build(lambda m: (deploy(m), ws(m, "web").update(acceptance_criteria=["C3"])))
        self.assertEqual(agreement.inherited(shared, "integration"), ["C1", "C2", "C3", "J1"])
        with self.assertRaisesRegex(ValueError, "C3 are assigned to no workstream"):
            build(lambda m: (deploy(m), ws(m, "deploy").pop("acceptance_criteria")))

    def test_dropped_reports_missing_inherited_ids_in_order(self):
        manifest = build()
        definitions = agreement.definitions(manifest, "integration")
        self.assertEqual(agreement.dropped(manifest, "integration", [definitions["C2"]]), ["C1", "J1"])
        self.assertEqual(agreement.dropped(manifest, "integration", [definitions["J1"]]), ["C1", "C2"])
        self.assertEqual(agreement.dropped(manifest, "integration", None), ["C1", "C2", "J1"])
        self.assertEqual(agreement.dropped(manifest, "engine", [definitions["C2"], {"id": "EXTRA"}]), [])
        self.assertEqual(agreement.dropped(manifest, "web", []), [])


class ScopeTest(unittest.TestCase):
    def test_consumer_membership_invalidates_changed_users_not_unchanged_producer(self):
        old = build()
        new = build(lambda m: iface(m, "api").update(consumers=["engine", "web"]))
        self.assertEqual([], agreement.revision_problems(old, new))
        self.assertEqual(["web", "integration"], agreement.affected(old, new))

    def test_a_revision_affects_only_the_workstreams_built_from_what_it_changed(self):
        def api_bump(m):
            iface(m, "api").update(version=2, schema={"next": "string"})

        cases = [
            ("requirement text", lambda m: m["requirements"][1].update(criterion="the engine adapts"),
             ["engine", "integration"]),
            ("workstream brief", lambda m: ws(m, "web").update(brief="web, accessible"), ["web"]),
            ("interface version and schema", api_bump, ["skeleton", "engine", "integration"]),
            ("interface without producer", lambda m: iface(m, "style").update(version=2, summary="new look"),
             ["skeleton", "engine", "web", "integration"]),
            ("journey", lambda m: m["journeys"][0]["steps"].append("see progress"), ["skeleton", "integration"]),
            ("shared constraints", lambda m: m["shared"]["constraints"].append("no network"),
             ["skeleton", "engine", "web", "integration"]),
            ("program outcome", lambda m: m.update(brief="A better outcome"),
             ["skeleton", "engine", "web", "integration"]),
        ]
        old = build()
        for label, change, expected in cases:
            with self.subTest(label):
                new = build(change)
                self.assertEqual(agreement.affected(old, new), expected)
                for wid in ("skeleton", "engine", "web", "integration"):
                    same = agreement.scope_digest(old, wid) == agreement.scope_digest(new, wid)
                    self.assertEqual(same, wid not in expected, wid)

    def test_the_parent_contract_is_in_every_workstreams_scope_but_its_criteria_and_milestones(self):
        def derived(m):
            m["contract"] = {"task_id": "t1", "revision": 1, "hash": "h1",
                             "body": {"intended_outcome": "Students practice lessons", "constraints": ["stdlib only"],
                                      "acceptance_criteria": m.pop("requirements"),
                                      "milestones": [{"id": "skeleton", "objective": "thin"},
                                                     {"id": "engine", "objective": "engine"}]}}

        def body(change):
            return lambda m: (derived(m), change(m["contract"]["body"]))

        every = ["skeleton", "engine", "web", "integration"]
        cases = [
            ("constraint", body(lambda b: b["constraints"].append("no network")), every),
            ("outcome", body(lambda b: b.update(intended_outcome="Students master lessons")), every),
            ("new section", body(lambda b: b.update(scope_exclusions=["payments"])), every),
            # Its criteria count, like requirements, only for the workstreams that inherit them.
            ("criterion text", body(lambda b: b["acceptance_criteria"][1].update(criterion="the engine adapts")),
             ["engine", "integration"]),
            ("criteria order", body(lambda b: b["acceptance_criteria"].reverse()), []),
            ("milestones", body(lambda b: (b["milestones"].reverse(), b["milestones"][1].update(objective="thinner"))),
             []),
            ("task id, revision and hash",
             lambda m: (derived(m), m["contract"].update(task_id="t2", revision=2, hash="h2")), []),
        ]
        old = build(derived)
        for label, change, expected in cases:
            with self.subTest(label):
                new = build(change)
                self.assertNotEqual(agreement.digest(old), agreement.digest(new))  # still a revision to approve
                self.assertEqual(agreement.affected(old, new), expected)

    def test_execution_details_change_the_agreement_but_no_workstream_scope(self):
        old = build(lambda m: m.update(checks=["make test"]))
        cases = [
            ("program checks", lambda m: m.update(checks=["make test", "make lint"])),
            ("workstream engine", lambda m: (m.update(checks=["make test"]), ws(m, "engine").update(engine="opencode"))),
        ]
        for label, change in cases:
            with self.subTest(label):
                new = build(change)
                self.assertNotEqual(agreement.digest(old), agreement.digest(new))
                self.assertEqual(agreement.affected(old, new), [])

    def test_digest_ignores_provenance(self):
        old = build()
        self.assertEqual(agreement.digest(old), agreement.digest(build(lambda m: m.update(source_run="run-7"))))


class RevisionTest(unittest.TestCase):
    def test_the_workstream_graph_and_name_are_frozen(self):
        old = build()
        topology = "ownership, dependencies or the skeleton changed"
        cases = [
            ("owns", build(lambda m: ws(m, "engine")["owns"].append("lib")), topology),
            ("depends_on", build(lambda m: ws(m, "web")["depends_on"].append("engine")), topology),
            ("skeleton_exempt", build(lambda m: ws(m, "web").update(kind="content", skeleton_exempt="static pages")), topology),
            ("name", build(lambda m: m.update(name="Demo 2")), "the program name changed"),
        ]
        # A kind or skeleton change cannot keep a manifest valid; revision_problems compares regardless.
        kind = build()
        ws(kind, "web")["kind"] = "deployment"
        moved = build()
        ws(moved, "skeleton")["skeleton"] = False
        ws(moved, "web")["skeleton"] = True
        cases += [("kind", kind, topology), ("skeleton flag", moved, topology)]
        for label, new, message in cases:
            with self.subTest(label):
                problems = agreement.revision_problems(old, new)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(message, problems[0])

    def test_order_and_spelled_out_defaults_are_not_graph_or_interface_changes(self):
        def base(m):
            add_workstream(m, {"id": "tools", "depends_on": ["skeleton"]})
            ws(m, "tools")["owns"] = ["tools", "scripts"]
            ws(m, "web")["skeleton_exempt"] = "static pages"
            ws(m, "web")["kind"] = "content"
            ws(m, "web")["depends_on"] = []
            iface(m, "api").update(consumers=["engine", "integration"], paths=["skeleton/api", "skeleton/schema"])

        def restated(m):
            base(m)
            ws(m, "tools")["owns"].reverse()
            ws(m, "integration")["depends_on"].reverse()
            ws(m, "engine")["skeleton"] = False
            ws(m, "web")["skeleton_exempt"] = "only static pages"
            iface(m, "api")["consumers"].reverse()
            iface(m, "api")["paths"].reverse()
            iface(m, "style").update(consumers=[], producer=None)

        old, new = build(base), build(restated)
        self.assertEqual(agreement.revision_problems(old, new), [])
        self.assertNotEqual(agreement.digest(old), agreement.digest(new))  # still a revision a person approves
        self.assertEqual(agreement.bumped_interfaces(old, new), {})
        self.assertEqual(agreement.affected(old, new), [])  # but no workstream is built from anything new

    def test_interface_definitions_change_only_with_a_new_version(self):
        old = build(lambda m: iface(m, "api").update(version=2))
        quiet = build(lambda m: iface(m, "api").update(version=2, summary="next activity JSON, with hints"))
        self.assertEqual(agreement.revision_problems(old, quiet),
                          ["interface api changed without a new version; publish the changed definition as exactly version 3"])
        back = build(lambda m: iface(m, "api").update(version=1))
        self.assertEqual(agreement.revision_problems(old, back), ["interface api has an invalid version; keep version 2 when its definition is unchanged"])

    def test_versions_never_skip_or_bump_an_unchanged_definition(self):
        old = build()
        for fields in ({"version": 3, "behavior": "changed"}, {"version": 2}):
            self.assertTrue(agreement.revision_problems(old, build(lambda m: iface(m, "api").update(fields))))

    def test_a_proper_revision_is_accepted(self):
        old = build()
        new = build(lambda m: (iface(m, "api").update(version=2, schema={"next": "string"}),
                               m["requirements"][1].update(criterion="the engine adapts"),
                               ws(m, "web").update(brief="web, accessible")))
        self.assertEqual(agreement.revision_problems(old, new), [])
        self.assertEqual(agreement.bumped_interfaces(old, new), {"api": (1, 2)})
        self.assertEqual(agreement.bumped_interfaces(old, old), {})


class QuietInterfaceChangeTest(unittest.TestCase):
    def test_delivered_interfaces_are_never_edited_in_place(self):
        manifest = build()
        quiet = agreement.quiet_interface_changes
        found = quiet(manifest, "engine", ["skeleton/api/next.json", "engine/core.py"], {})
        self.assertEqual([iid for iid, _ in found], ["api"])
        self.assertIn("engine is not its producer (skeleton)", found[0][1])
        self.assertIn("skeleton/api/next.json", found[0][1])
        self.assertNotIn("engine/core.py", found[0][1])

        found = quiet(manifest, "skeleton", ["skeleton/api/next.json"], {"api": 1})
        self.assertEqual([iid for iid, _ in found], ["api"])
        self.assertIn("version 1 was already delivered", found[0][1])

        self.assertEqual(quiet(manifest, "skeleton", ["skeleton/api/next.json"], {}), [])
        bumped = build(lambda m: iface(m, "api").update(version=2))
        self.assertEqual(quiet(bumped, "skeleton", ["skeleton/api/next.json"], {"api": 1}), [])

        found = quiet(manifest, "integration", ["web/style/site.css"], {})
        self.assertEqual([iid for iid, _ in found], ["style"])
        self.assertIn("integration workstream changed web/style/site.css", found[0][1])

    def test_paths_outside_interfaces_are_never_flagged(self):
        manifest = build()
        paths = ["skeleton/apiary/x.py", "skeleton/main.py", "web/styles.css", "engine/core.py"]
        for wid in ("skeleton", "engine", "web", "integration"):
            with self.subTest(wid):
                self.assertEqual(agreement.quiet_interface_changes(manifest, wid, paths, {"api": 1}), [])


class PresentationTest(unittest.TestCase):
    def test_changes_name_what_a_revision_changed(self):
        old = build()
        new = build(lambda m: (m["requirements"][1].update(criterion="the engine adapts"),
                               m["journeys"][0]["steps"].append("see progress"),
                               iface(m, "api").update(version=2, schema={"next": "string"}),
                               ws(m, "web").update(brief="web, accessible")))
        self.assertEqual(agreement.changes(old, new),
                         ["requirement C2 changed", "journey J1 changed", "interface api v1 -> v2",
                          "workstream web brief"])
        self.assertEqual(agreement.changes(old, old), [])

    def test_changes_name_contract_milestones_notes_and_reordering(self):
        def derived(m):
            m["contract"] = {"revision": 1, "body": {"acceptance_criteria": m.pop("requirements"),
                                                    "milestones": [{"id": "skeleton", "objective": "thin"}]}}
            m["derivation_notes"] = ["web had no dependencies"]
            add_workstream(m, {"id": "tools", "depends_on": ["skeleton"], "owns": ["tools", "scripts"]})

        def revised(m):
            derived(m)
            m["contract"]["body"]["milestones"][0]["objective"] = "thinner"
            m["derivation_notes"].append("integration added")
            m["contract"]["body"]["acceptance_criteria"].reverse()
            ws(m, "tools")["owns"].reverse()
            m["shared"]["interfaces"].reverse()

        old = build(derived)
        self.assertEqual(agreement.changes(old, build(revised)),
                         ["parent contract milestones", "order of requirements", "order of interfaces",
                          "workstream tools owns order", "derivation notes"])
        quiet = build(lambda m: (derived(m), m["contract"].update(revision=2)))
        self.assertEqual(agreement.changes(old, quiet), ["parent contract revision"])
        extra = build(lambda m: (derived(m), m["shared"].update(note="kept for later")))
        self.assertEqual(agreement.changes(old, extra),
                         ["the manifest changed in a way no workstream is built from: shared"])

    def test_render_shows_what_a_person_approves(self):
        manifest = build(lambda m: (m["journeys"].append(
            {"id": "J2", "name": "Checkout", "steps": ["pay"], "simulated": True,
             "does_not_prove": "a real payment provider"}), ws(m, "engine").update(checks=["make engine-test"])))
        value = agreement.digest(manifest)
        text = agreement.render(manifest, revision=3, value=value)
        self.assertEqual(agreement.token(3, value), f"a3:{value}")
        self.assertIn(f"Approve with token: a3:{value}", text)
        self.assertIn("J1 Student practice: read lesson -> answer with hint -> see next activity", text)
        self.assertIn("J2 Checkout: pay", text)
        self.assertIn("simulated; does not prove: a real payment provider", text)
        self.assertIn("- skeleton (walking skeleton, built and verified first) owns skeleton; depends on nothing", text)
        self.assertIn("inherits: C1, J1, J2\n", text)
        self.assertIn("inherits: C1, C2, J1, J2", text)
        self.assertIn("  engine\n  checks, re-run on the integration branch after every merge:\n  - make engine-test\n",
                      text)
        self.assertIn("- api v1: next activity JSON; produced by skeleton, used by engine [skeleton/api]", text)
        self.assertIn("- style v1: shared look [web/style]", text)
        self.assertNotIn("Changes since the approved revision", text)

    def test_render_with_a_previous_revision_names_the_affected_workstreams(self):
        old = build()
        new = build(lambda m: m["requirements"][1].update(criterion="the engine adapts"))
        text = agreement.render(new, revision=2, value=agreement.digest(new), previous=old)
        self.assertIn("- requirement C2 changed", text)
        self.assertIn("must be planned, approved and checked again: engine, integration", text)
        same = agreement.render(old, revision=2, value=agreement.digest(old), previous=old)
        self.assertIn("must be planned, approved and checked again: none", same)


if __name__ == "__main__":
    unittest.main()
