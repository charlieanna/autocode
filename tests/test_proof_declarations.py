"""Proof declaration grammar and the existing runner binding/fallback boundary."""

import copy
import unittest

import autocode_proof_declarations as declarations
import autocode_test_cases as test_cases


class ProofDeclarationTests(unittest.TestCase):
    def test_exact_live_plan_selectors_are_refused_together_without_mutation(self):
        selectors = [
            "tests.test_health.HealthTests.test_health",
            "tests.test_banner.BannerTests.test_nova",
            "tests.test_banner.BannerTests.test_empty",
            "tests.test_banner.BannerTests.test_spaces",
            "tests.test_journey.JourneyTests.test_health_then_banner",
            "tests.test_seed.SeedTests.test_seed_health",
            "tests.test_seed.SeedTests.test_seed_has_no_new_api",
        ]
        criteria = [
            {"id": f"AC{i}", "verification_method": f"{'guard' if i >= 6 else 'test'}: {selector}"}
            for i, selector in enumerate(selectors, 1)
        ]
        criteria += [
            {"id": "AC8", "verification_method": "Independently inspect source and proof"},
            {"id": "AC9", "verification_method": "Run the seven-test suite and fresh journey command"},
        ]
        before = copy.deepcopy(criteria)
        with self.assertRaisesRegex(ValueError, "Unsupported qualified Python proof selector") as refused:
            declarations.validate(criteria)
        for i, selector in enumerate(selectors, 1):
            self.assertIn(f"AC{i} declares {selector!r}", str(refused.exception))
        self.assertNotIn("AC8 declares", str(refused.exception))
        self.assertEqual(before, criteria)

    def test_one_qualified_selector_is_refused_with_supported_annotations(self):
        for declaration in (
            "test: tests.test_health.HealthTests.test_health",
            "guard: HealthTests.test_health",
            " TEST: tests.test_health.test_health — independent check",
            "guard: tests.test_seed.SeedTests.test_seed_health (python3 -m unittest -v tests.test_seed)",
            "test: tests.test_health.HealthTests.test_health - check the real API",
        ):
            with self.subTest(declaration=declaration):
                self.assertIsNotNone(declarations.unsupported_python_selector(declaration))
                with self.assertRaisesRegex(ValueError, "C1 declares"):
                    declarations.validate([{"id": "C1", "verification_method": declaration}])

    def test_paired_code_wrappers_do_not_hide_a_single_qualified_selector(self):
        selector = "tests.test_health.HealthTests.test_health"
        for declaration in (
            f"test: `{selector}`",
            f"guard: ``{selector}``",
            f" TEST: `{selector}` — independent check",
            f"guard: ``{selector}`` (python3 -m unittest -v tests.test_health)",
            f"test: `{selector}` - check the real API",
            f"test: ` {selector} `",
            f"guard: `` {selector} `` (python3 -m unittest -v tests.test_health)",
        ):
            with self.subTest(declaration=declaration):
                criteria = [{"id": "C1", "verification_method": declaration}]
                before = copy.deepcopy(criteria)
                self.assertEqual(selector, declarations.unsupported_python_selector(declaration))
                with self.assertRaisesRegex(ValueError, "C1 declares"):
                    declarations.validate(criteria)
                self.assertEqual(before, criteria)

    def test_supported_names_and_deliberate_fallback_keep_the_same_runner_cases(self):
        for declaration, name in (
            ("test: test_health", "test_health"),
            ("guard: test_seed_health (python3 -m unittest -v tests.test_seed)", "test_seed_health"),
            ("test: test_C1_health — inspect module.Class.test_health too", "test_C1_health"),
            ("test: TestHealth", "TestHealth"),
            (
                "guard: TestHealth/Package.Case_Test/Run_-_creates (go test ./...)",
                "TestHealth/Package.Case_Test/Run_-_creates",
            ),
            ("test: test_c1_<what it checks>", None),
            ("test: Check tests.test_health.HealthTests.test_health in the source", None),
            ("guard: test_c4_add_still_works, test_c4_adds_zero", None),
            ("guard: test_c4_add_still_works and test_c4_adds_zero", None),
            ("test: tests.test_health.HealthTests.test_health, test_other", None),
            ("test: tests.test_health.HealthTests.test_health and test_other", None),
            ("test: `test_health`", None),
            ("guard: `TestHealth/Package.Case_Test`", None),
            ("test: `test_c1_<what it checks>`", None),
            ("test: `Check module.Case.test_health in the source`", None),
            ("test: `module.Case.test_health` and test_other", None),
            ("test: `module.Case.test_health, test_other`", None),
            ("test: `module.Case.test_health``", None),
        ):
            with self.subTest(declaration=declaration):
                criteria = [{"id": "C1", "criterion": "Preserve behavior", "verification_method": declaration}]
                before = copy.deepcopy(criteria)
                declarations.validate(criteria)
                cases = test_cases.plan_cases({"acceptance_criteria": criteria})
                self.assertEqual(name, cases[0].get("test_name"))
                self.assertEqual("preserve" if declaration.startswith("guard:") else None, cases[0].get("kind"))
                self.assertEqual(before, criteria)
        for method in ("Read tests.test_health.HealthTests.test_health", "", None):
            with self.subTest(method=method):
                declarations.validate([{"id": "C1", "verification_method": method}])
                self.assertIsNone(declarations.unsupported_python_selector(method))
