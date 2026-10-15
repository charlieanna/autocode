"""Synthetic pure-policy controls; no native/source qualification is implied."""

import copy
import hashlib
import json
import unittest

import autocode_design_scope as scope
import autocode_util as util  # noqa: F401 - preserve existing import behavior


def synthetic():
    file, page = "FileA", "1:1"
    nodes, parents = {}, {}

    def add(node_id, kind, parent, **fields):
        identity = (file, page, node_id)
        nodes[identity] = {
            "id": node_id,
            "type": kind,
            "name": node_id,
            "variables": [],
            "fonts": [],
            "assets": [],
            "transitions": [],
            **fields,
        }
        parents[identity] = (file, page, parent) if parent else None
        return identity

    add(page, "PAGE", None)
    app = add("1:2", "FRAME", page)
    history = add("1:3", "FRAME", page)
    add(
        "1:4",
        "INSTANCE",
        app[2],
        component_ref={"key": "set-key", "node_id": "1:11", "variant_properties": {"State": "One"}},
        visual={"main_component_owner": {"id": "1:10", "type": "COMPONENT_SET"}},
    )
    add("1:5", "TEXT", history[2], fonts=[{"family": "Legacy", "style": "Regular"}])
    owner = add("1:10", "COMPONENT_SET", page, component_key="set-key")
    first = add("1:11", "COMPONENT", owner[2], component_key="main-key", variant_properties={"State": "One"})
    second = add("1:12", "COMPONENT", owner[2], component_key="second-key", variant_properties={"State": "Two"})
    add("1:13", "TEXT", first[2], fonts=[{"family": "Inter", "style": "Regular"}])
    add(
        "1:14",
        "INSTANCE",
        second[2],
        component_ref={"key": "nested-key", "node_id": "1:20", "variant_properties": {}},
        visual={"main_component_owner": {"id": "1:20", "type": "COMPONENT"}},
    )
    nested = add("1:20", "COMPONENT", page, component_key="nested-key", variant_properties={})
    reaction = {"id": "transition-used", "target_node_id": first[2], "action": {"destinationId": first[2]}}
    add(
        "1:21",
        "TEXT",
        nested[2],
        fonts=[{"family": "Inter", "style": "Semi Bold"}],
        variables=["Variable:Alias"],
        assets=[{"id": "asset-used"}],
        transitions=[reaction],
    )
    legacy = add("1:30", "COMPONENT", page, component_key="legacy-key", variant_properties={})
    add("1:31", "TEXT", legacy[2], fonts=[{"family": "Legacy", "style": "Regular"}])
    component = lambda key, source, variants: {
        "key": key,
        "source_page_id": page,
        "source_node_id": source,
        "variants": [
            {"page_id": page, "node_id": node[2], "properties": nodes[node]["variant_properties"]} for node in variants
        ],
    }
    resources = {
        "components": {
            (file, "set-key"): component("set-key", owner[2], [first, second]),
            (file, "nested-key"): component("nested-key", nested[2], []),
            (file, "legacy-key"): component("legacy-key", legacy[2], []),
        },
        "variables": {
            (file, "alias-key"): {
                "key": "alias-key",
                "source_id": "Variable:Alias",
                "value": {
                    "light": {"type": "VARIABLE_ALIAS", "id": "Variable:Light"},
                    "dark": {"type": "VARIABLE_ALIAS", "id": "Variable:Dark"},
                },
            },
            (file, "light-mode-1"): {"key": "light-mode-1", "source_id": "Variable:Light", "value": 12},
            (file, "light-mode-2"): {"key": "light-mode-2", "source_id": "Variable:Light", "value": 16},
            (file, "dark-key"): {"key": "dark-key", "source_id": "Variable:Dark", "value": 20},
            (file, "historical-token"): {"key": "historical-token", "source_id": "Variable:History", "value": 24},
        },
        "fonts": {
            (file, key): {"id": key, "family": family, "style": style}
            for key, family, style in [
                ("font-regular", "Inter", "Regular"),
                ("font-semibold", "Inter", "Semi Bold"),
                ("font-legacy", "Legacy", "Regular"),
            ]
        },
        "assets": {(file, "asset-used"): {"id": "asset-used"}, (file, "asset-history"): {"id": "asset-history"}},
        "transitions": {
            (file, "transition-used"): {"id": "transition-used"},
            (file, "transition-history"): {"id": "transition-history"},
        },
    }
    facts = {"nodes": nodes, "parents": parents, "resources": resources}
    owners = {"main-key": [first], "nested-key": [nested]}
    cases = [
        {
            "id": "app." + state,
            "file_key": file,
            "page_id": page,
            "node_id": app[2],
            "state": state,
            "viewport": {"width": 800, "height": 600, "device_scale_factor": 1},
            "artifacts": {"screenshot": {"path": state + ".png", "sha256": "c" * 64}},
        }
        for state in ["light", "dark"]
    ]
    return facts, owners, cases, [app, history]


def captured_set_identity():
    """Exact retained instance/main/set identity, with declared pure test facts.

    Captured in the approved AutoCode file at422:1259 and289:15/289:87.
    This focused identity fixture is not a complete native qualification bundle.
    Both original keys, all six variant IDs/coordinates and topology are kept.
    """
    file, app_page, library_page = "kcSv6tbrqK3pzMFl2BRSUk", "422:1179", "288:2"
    set_key = "4e18b2a2c52a3e09ce9f8d2bc02b1fcfcee2fbd3"
    rows = [
        ("289:15", "dcf2a49d92f2c70d71064438e1248774310dcc68", "Neutral"),
        ("289:27", "d6875e2d25eaa15f4c617f1dfb912b07a46a2885", "Action"),
        ("289:39", "9b276e1e0a1fc0ece97580fab760562e22fadc75", "Success"),
        ("289:51", "0d228ba982efd259de3708c14fe2f424d535e843", "Warning"),
        ("289:63", "522b90a6718589666cebff76efdc65b8746192a9", "Danger"),
        ("289:75", "ea8c23a5039148b221d0f26d4cd03e15fb8990ae", "Frozen"),
    ]
    nodes, parents = {}, {}

    def add(page, node_id, kind, parent, **values):
        identity = (file, page, node_id)
        nodes[identity] = dict(id=node_id, type=kind, variables=[], fonts=[], assets=[], transitions=[], **values)
        parents[identity] = (file, page, parent) if parent else None
        return identity

    add(app_page, app_page, "PAGE", None)
    parent = app_page
    for node_id in ["422:1180", "422:1242", "422:1248", "422:1249", "422:1250"]:
        add(app_page, node_id, "FRAME", parent)
        parent = node_id
    add(
        app_page,
        "422:1259",
        "INSTANCE",
        parent,
        component_ref={"key": set_key, "node_id": "289:15", "variant_properties": {"Tone": "Neutral"}, "remote": False},
        visual={"main_component_owner": {"id": "289:87", "type": "COMPONENT_SET"}},
    )
    add(library_page, library_page, "PAGE", None)
    add(library_page, "289:87", "COMPONENT_SET", library_page, component_key=set_key)
    owners, variants = {}, []
    for node_id, key, tone in rows:
        identity = add(
            library_page, node_id, "COMPONENT", "289:87", component_key=key, variant_properties={"Tone": tone}
        )
        owners[key] = [identity]
        variants.append({"page_id": library_page, "node_id": node_id, "properties": {"Tone": tone}})
    resources = {section: {} for section in scope.SECTIONS}
    resources["components"][(file, set_key)] = {
        "key": set_key,
        "source_page_id": library_page,
        "source_node_id": "289:87",
        "variants": variants,
    }
    cases = [{"id": "native.identity", "file_key": file, "page_id": app_page, "node_id": "422:1180"}]
    return {"nodes": nodes, "parents": parents, "resources": resources}, owners, cases


def authority(cases, manifest_hash="a" * 64):
    artifact = {
        "version": 1,
        "manifest_hash": manifest_hash,
        "approved_case_ids": [case["id"] for case in cases],
        "approval_evidence": [
            {"kind": "approved_brief", "sha256": "b" * 64},
            {"kind": "reviewed_native_case_binding", "sha256": "d" * 64},
        ],
    }
    raw = json.dumps(artifact).encode()
    return raw, hashlib.sha256(raw).hexdigest(), manifest_hash, cases


class DesignScopeTests(unittest.TestCase):
    def setUp(self):
        self.facts, self.owners, self.cases, self.frames = synthetic()

    def computed(self):
        return scope.compute_use(self.facts, self.owners, self.cases)

    def test_captured_set_reference_keeps_distinct_main_and_owner_keys(self):
        facts, owners, cases = captured_set_identity()
        before = copy.deepcopy((facts, owners, cases))
        result = scope.compute_use(facts, owners, cases)
        file = cases[0]["file_key"]
        self.assertEqual(
            result["cases"]["native.identity"]["components"], ((file, "4e18b2a2c52a3e09ce9f8d2bc02b1fcfcee2fbd3"),)
        )
        self.assertEqual(
            result["component_variants"]["native.identity"],
            tuple((file, "288:2", node) for node in ["289:15", "289:27", "289:39", "289:51", "289:63", "289:75"]),
        )
        self.assertNotEqual(
            facts["nodes"][(file, "288:2", "289:15")]["component_key"],
            facts["nodes"][(file, "422:1179", "422:1259")]["component_ref"]["key"],
        )
        self.assertEqual((facts, owners, cases), before)

    def test_captured_set_reference_rejects_forged_main_parent_key_and_coordinates(self):
        for change in ["main", "parent", "key", "coordinates", "missing-index", "invented-index"]:
            facts, owners, cases = captured_set_identity()
            file = cases[0]["file_key"]
            instance = facts["nodes"][(file, "422:1179", "422:1259")]
            main = (file, "288:2", "289:15")
            main_key = facts["nodes"][main]["component_key"]
            if change == "main":
                instance["component_ref"]["node_id"] = "289:27"
            elif change == "parent":
                facts["parents"][main] = (file, "288:2", "288:2")
            elif change == "key":
                instance["component_ref"]["key"] = "unknown-owner-key"
            elif change == "coordinates":
                instance["component_ref"]["variant_properties"] = {"Tone": "Action"}
            elif change == "missing-index":
                owners.pop(main_key)
            else:
                owners[main_key].append((file, "288:2", "289:27"))
            with self.subTest(change=change), self.assertRaises(ValueError):
                scope.compute_use(facts, owners, cases)

    def test_shared_main_key_cannot_claim_a_different_promoted_owner(self):
        facts, owners, cases = captured_set_identity()
        file = cases[0]["file_key"]
        main_key = facts["nodes"][(file, "288:2", "289:15")]["component_key"]
        page, parent, main = ("OtherFile", "2:1", "2:1"), ("OtherFile", "2:1", "2:10"), ("OtherFile", "2:1", "2:11")
        for identity, kind, ancestor, key in [
            (page, "PAGE", None, None),
            (parent, "COMPONENT_SET", page, "conflicting-owner-key"),
            (main, "COMPONENT", parent, main_key),
        ]:
            facts["nodes"][identity] = dict(
                id=identity[2], type=kind, variables=[], fonts=[], assets=[], transitions=[]
            )
            facts["parents"][identity] = ancestor
            if key:
                facts["nodes"][identity]["component_key"] = key
        facts["nodes"][main]["variant_properties"] = {"Tone": "Neutral"}
        owners[main_key].append(main)
        with self.assertRaisesRegex(ValueError, "owner.*key|key.*owner"):
            scope.compute_use(facts, owners, cases)

    def test_shared_main_key_coordinates_must_match_original_reference(self):
        facts, owners, cases = captured_set_identity()
        file = cases[0]["file_key"]
        original_main = (file, "288:2", "289:15")
        main_key = facts["nodes"][original_main]["component_key"]
        set_key = facts["nodes"][(file, "288:2", "289:87")]["component_key"]
        page, parent, main = ("OtherFile", "2:1", "2:1"), ("OtherFile", "2:1", "2:10"), ("OtherFile", "2:1", "2:11")
        for identity, kind, ancestor, key in [
            (page, "PAGE", None, None),
            (parent, "COMPONENT_SET", page, set_key),
            (main, "COMPONENT", parent, main_key),
        ]:
            facts["nodes"][identity] = dict(
                id=identity[2], type=kind, variables=[], fonts=[], assets=[], transitions=[]
            )
            facts["parents"][identity] = ancestor
            if key:
                facts["nodes"][identity]["component_key"] = key
        facts["nodes"][main]["variant_properties"] = {"Tone": "Neutral"}
        declared = {
            "key": set_key,
            "source_page_id": "2:1",
            "source_node_id": "2:10",
            "variants": [{"page_id": "2:1", "node_id": "2:11", "properties": {"Tone": "Neutral"}}],
        }
        facts["resources"]["components"][("OtherFile", set_key)] = declared
        owners[main_key].append(main)
        used = scope.compute_use(facts, owners, cases)
        self.assertIn(("OtherFile", set_key), used["cases"]["native.identity"]["components"])
        # Each copied source and its local declaration agree, but the same
        # stable main identity cannot acquire different variant coordinates.
        facts["nodes"][main]["variant_properties"] = {"Tone": "Action"}
        declared["variants"][0]["properties"] = {"Tone": "Action"}
        self.assertEqual(facts["nodes"][original_main]["variant_properties"], {"Tone": "Neutral"})
        with self.assertRaisesRegex(ValueError, "variant coordinates"):
            scope.compute_use(facts, owners, cases)

    def test_external_pin_admits_exact_case_packet_and_frame_complement(self):
        admission = scope.admit(*authority(self.cases))
        app, retained = scope.partition(self.frames, self.cases, admission)
        self.assertEqual(app, (self.frames[0],))
        self.assertEqual(retained, (self.frames[1],))
        self.assertEqual(set(app) | set(retained), set(self.frames))
        self.assertFalse(set(app) & set(retained))

    def test_legacy_no_admission_keeps_all_frame_coverage_required(self):
        with self.assertRaisesRegex(ValueError, "Legacy"):
            scope.partition(self.frames, self.cases, None)
        self.assertEqual(scope.partition(self.frames[:1], self.cases, None), ((self.frames[0],), ()))

    def test_missing_or_wrong_external_pin_cannot_self_issue_approval(self):
        raw, pin, manifest, cases = authority(self.cases)
        for bad_pin in [None, "", "f" * 64]:
            with self.subTest(pin=bad_pin), self.assertRaises(ValueError):
                scope.admit(raw, bad_pin, manifest, cases)

    def test_authority_rejects_another_body_and_same_frame_missing_state(self):
        raw, pin, manifest, cases = authority(self.cases)
        with self.assertRaisesRegex(ValueError, "another complete manifest"):
            scope.admit(raw, pin, "f" * 64, cases)
        with self.assertRaisesRegex(ValueError, "exactly"):
            scope.admit(raw, pin, manifest, cases[:1])
        admission = scope.admit(raw, pin, manifest, cases)
        for changed in [
            cases[:1],
            [{**cases[0], "state": "changed"}, cases[1]],
            [{**cases[0], "viewport": {"width": 600}}, cases[1]],
            [{**cases[0], "artifacts": {}}, cases[1]],
        ]:
            with self.subTest(cases=changed), self.assertRaisesRegex(ValueError, "packet changed"):
                scope.partition(self.frames, changed, admission)

    def test_authority_rejects_duplicate_json_fields_and_unknown_schema(self):
        raw, _, manifest, cases = authority(self.cases)
        malformed = raw.replace(b'"version": 1', b'"version": 1, "version": 1')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            scope.admit(malformed, hashlib.sha256(malformed).hexdigest(), manifest, cases)
        for field, value in [
            ("version", True),
            ("unreviewed", True),
            ("approval_evidence", []),
            ("approved_case_ids", [cases[0]["id"], cases[0]["id"]]),
            ("approval_evidence", [{"kind": "provider_claim", "sha256": "d" * 64}]),
        ]:
            artifact = json.loads(raw)
            artifact[field] = value
            candidate = json.dumps(artifact).encode()
            with self.subTest(field=field), self.assertRaises(ValueError):
                scope.admit(candidate, hashlib.sha256(candidate).hexdigest(), manifest, cases)

    def test_partition_refuses_unknown_or_duplicated_source_frame(self):
        admission = scope.admit(*authority(self.cases))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            scope.partition(self.frames + self.frames[:1], self.cases, admission)
        with self.assertRaisesRegex(ValueError, "actual full-source"):
            scope.partition(self.frames[1:], self.cases, admission)

    def test_closure_includes_all_variants_nested_owners_aliases_and_modes(self):
        used = self.computed()
        expected = used["cases"]["app.light"]
        self.assertEqual(expected["components"], (("FileA", "nested-key"), ("FileA", "set-key")))
        self.assertEqual(expected["fonts"], (("FileA", "font-regular"), ("FileA", "font-semibold")))
        self.assertEqual(
            expected["variables"],
            tuple(("FileA", key) for key in ["alias-key", "dark-key", "light-mode-1", "light-mode-2"]),
        )
        self.assertEqual(expected["assets"], (("FileA", "asset-used"),))
        self.assertEqual(expected["transitions"], (("FileA", "transition-used"),))
        self.assertEqual(used["component_variants"]["app.light"], (("FileA", "1:1", "1:11"), ("FileA", "1:1", "1:12")))
        self.assertIn(("FileA", "1:1", "1:21"), used["used_nodes"]["app.light"])
        self.assertNotIn(("FileA", "1:1", "1:31"), used["used_nodes"]["app.light"])
        self.assertEqual(expected, used["cases"]["app.dark"])

    def test_embedded_owner_getter_cannot_replace_missing_original_definition(self):
        self.owners.pop("main-key")
        with self.assertRaisesRegex(ValueError, "Unresolved actual"):
            self.computed()
        self.owners["main-key"] = [("FileA", "1:1", "1:12")]
        with self.assertRaisesRegex(ValueError, "Original main component"):
            self.computed()

    def test_native_owner_index_and_getter_must_match_actual_definitions(self):
        self.owners["main-key"] = [self.frames[0]]
        with self.assertRaisesRegex(ValueError, "Original main component"):
            self.computed()
        self.owners["main-key"] = [("FileA", "1:1", "1:11")]
        self.facts["nodes"][("FileA", "1:1", "1:4")]["visual"]["main_component_owner"]["id"] = "1:30"
        with self.assertRaisesRegex(ValueError, "getter owner"):
            self.computed()

    def test_used_set_missing_variant_is_a_closure_failure(self):
        self.facts["resources"]["components"][("FileA", "set-key")]["variants"].pop()
        with self.assertRaisesRegex(ValueError, "omits or invents actual variants"):
            self.computed()

    def test_standalone_owner_cannot_claim_its_own_set_variant(self):
        self.facts["resources"]["components"][("FileA", "nested-key")]["variants"] = [
            {"page_id": "1:1", "node_id": "1:20", "properties": {}}
        ]
        with self.assertRaisesRegex(ValueError, "Standalone"):
            self.computed()

    def test_missing_nested_owner_and_alias_mode_are_explicit_blockers(self):
        self.owners.pop("nested-key")
        with self.assertRaisesRegex(ValueError, "Unresolved actual"):
            self.computed()
        self.owners["nested-key"] = [("FileA", "1:1", "1:20")]
        self.facts["resources"]["variables"].pop(("FileA", "dark-key"))
        with self.assertRaisesRegex(ValueError, "alias/mode"):
            self.computed()

    def add_second_actual_owner_source(self):
        page, main, text = ("FileB", "2:1", "2:1"), ("FileB", "2:1", "2:20"), ("FileB", "2:1", "2:21")
        for identity, kind, parent in [(page, "PAGE", None), (main, "COMPONENT", page), (text, "TEXT", main)]:
            self.facts["nodes"][identity] = {
                "id": identity[2],
                "type": kind,
                "name": identity[2],
                "variables": [],
                "fonts": [],
                "assets": [],
                "transitions": [],
            }
            self.facts["parents"][identity] = parent
        self.facts["nodes"][main]["component_key"] = "nested-key"
        self.facts["nodes"][main]["variant_properties"] = {}
        self.facts["nodes"][text]["fonts"] = [{"family": "Inter", "style": "Semi Bold"}]
        self.facts["resources"]["components"][("FileB", "nested-key")] = {
            "key": "nested-key",
            "source_page_id": "2:1",
            "source_node_id": "2:20",
            "variants": [],
        }
        self.facts["resources"]["fonts"][("FileB", "font-semibold-b")] = {
            "id": "font-semibold-b",
            "family": "Inter",
            "style": "Semi Bold",
        }
        return main

    def test_shared_actual_owner_sources_keep_file_local_resources(self):
        main = self.add_second_actual_owner_source()
        self.owners["nested-key"].append(main)
        used = self.computed()["cases"]["app.light"]
        self.assertIn(("FileB", "nested-key"), used["components"])
        self.assertIn(("FileB", "font-semibold-b"), used["fonts"])
        self.assertIn(("FileA", "font-semibold"), used["fonts"])

    def test_owner_index_cannot_hide_an_actual_shared_source(self):
        self.add_second_actual_owner_source()
        with self.assertRaisesRegex(ValueError, "omits or invents actual component"):
            self.computed()

    def test_shared_variable_key_preserves_each_local_alias_and_mode(self):
        variables = self.facts["resources"]["variables"]
        variables[("FileB", "alias-key")] = {
            "key": "alias-key",
            "source_id": "Variable:AliasB",
            "value": {
                "light": {"type": "VARIABLE_ALIAS", "id": "Variable:LightB"},
                "dark": {"type": "VARIABLE_ALIAS", "id": "Variable:DarkB"},
            },
        }
        for key, source, value in [
            ("light-mode-1", "Variable:LightB", 12),
            ("light-mode-2", "Variable:LightB", 16),
            ("dark-key", "Variable:DarkB", 20),
        ]:
            variables[("FileB", key)] = {"key": key, "source_id": source, "value": value}
        used = self.computed()["cases"]["app.light"]["variables"]
        self.assertEqual(
            set(used),
            {
                (file, key)
                for file in ["FileA", "FileB"]
                for key in ["alias-key", "light-mode-1", "light-mode-2", "dark-key"]
            },
        )
        variables.pop(("FileB", "dark-key"))
        with self.assertRaisesRegex(ValueError, "alias/mode"):
            self.computed()

    def test_required_historical_prototype_target_is_not_silently_excluded(self):
        transition = self.facts["nodes"][("FileA", "1:1", "1:21")]["transitions"][0]
        transition["target_node_id"] = "1:3"
        transition["action"]["destinationId"] = "1:3"
        with self.assertRaisesRegex(ValueError, "outside approved"):
            self.computed()

    def test_unknown_nested_prototype_target_refuses(self):
        transition = self.facts["nodes"][("FileA", "1:1", "1:21")]["transitions"][0]
        transition["action"]["conditional"] = [{"destinationId": "9:9"}]
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            self.computed()

    def test_exact_use_preserves_full_resources_as_disjoint_complement(self):
        computed = self.computed()
        full = {section: tuple(rows) for section, rows in self.facts["resources"].items()}
        result = scope.require_exact_use(computed["cases"], computed, full)
        for section in scope.SECTIONS:
            self.assertEqual(set(result["used"][section]) | set(result["retained"][section]), set(full[section]))
            self.assertFalse(set(result["used"][section]) & set(result["retained"][section]))
        self.assertEqual(result["retained"]["components"], (("FileA", "legacy-key"),))
        self.assertEqual(result["retained"]["variables"], (("FileA", "historical-token"),))

    def test_false_extra_usage_missing_used_dependency_and_duplicate_refs_refuse(self):
        computed = self.computed()
        full = {section: tuple(rows) for section, rows in self.facts["resources"].items()}
        for mutate in [
            lambda rows: rows + (("FileA", "font-legacy"),),
            lambda rows: rows[:1],
            lambda rows: rows + rows[:1],
        ]:
            refs = copy.deepcopy(computed["cases"])
            refs["app.light"]["fonts"] = mutate(refs["app.light"]["fonts"])
            with self.assertRaisesRegex(ValueError, "omit actual use or claim false use"):
                scope.require_exact_use(refs, computed, full)

    def test_used_resource_cannot_be_lost_from_full_universe(self):
        computed = self.computed()
        full = {section: tuple(rows) for section, rows in self.facts["resources"].items()}
        full["fonts"] = (("FileA", "font-legacy"),)
        with self.assertRaisesRegex(ValueError, "complete resource universe"):
            scope.require_exact_use(computed["cases"], computed, full)

    def test_reordered_facts_are_deterministic_and_inputs_unchanged(self):
        original = copy.deepcopy((self.facts, self.owners, self.cases))
        first = self.computed()
        self.facts["nodes"] = dict(reversed(list(self.facts["nodes"].items())))
        self.facts["parents"] = dict(reversed(list(self.facts["parents"].items())))
        self.facts["resources"] = {
            section: dict(reversed(list(rows.items()))) for section, rows in self.facts["resources"].items()
        }
        self.assertEqual(first, self.computed())
        self.assertEqual(self.facts, original[0])
        self.assertEqual(self.owners, original[1])
        self.assertEqual(self.cases, original[2])


if __name__ == "__main__":
    unittest.main()
