"""Catalogue search + role suggestion (offline; no live opencode spend)."""
from __future__ import annotations

# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import sys
import unittest
from pathlib import Path

HERE = _TOOLS
_ROOT = _Path(__file__).resolve().parents[1] if _Path(__file__).name != 'live_trial.py' else _Path(__file__).resolve().parent.parent
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)

import model_catalogue as mc  # noqa: E402


CATALOGUE = [
    "zai-coding-plan/glm-5.3",
    "zai-coding-plan/glm-5.3-flash",
    "zai-coding-plan/glm-5.2-highspeed",
    "xiaomi-token-plan-sgp/mimo-v2.6-pro",
    "xiaomi-token-plan-sgp/mimo-v2.6-flash",
    "mimo-token-plan/mimo-v2.6-pro",
    "opencode/mimo-v2.6-flash-free",
    "openai/gpt-5.6-sol",
    "not a model",
]


class UsableTest(unittest.TestCase):
    def test_drops_free_flash_highspeed_and_dead_routes(self):
        kept = mc.usable(CATALOGUE)
        self.assertIn("zai-coding-plan/glm-5.3", kept)
        self.assertIn("xiaomi-token-plan-sgp/mimo-v2.6-pro", kept)
        self.assertIn("openai/gpt-5.6-sol", kept)
        self.assertNotIn("zai-coding-plan/glm-5.3-flash", kept)
        self.assertNotIn("zai-coding-plan/glm-5.2-highspeed", kept)
        self.assertNotIn("xiaomi-token-plan-sgp/mimo-v2.6-flash", kept)
        self.assertNotIn("mimo-token-plan/mimo-v2.6-pro", kept)
        self.assertNotIn("opencode/mimo-v2.6-flash-free", kept)
        self.assertNotIn("not a model", kept)


class SuggestTest(unittest.TestCase):
    def test_prefers_subscription_pair_when_present(self):
        roles = mc.suggest(mc.usable(CATALOGUE))
        self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", roles["builder"]["model"])
        self.assertEqual("medium", roles["builder"]["effort"])
        self.assertEqual("zai-coding-plan/glm-5.3", roles["validator"]["model"])
        self.assertEqual("zai-coding-plan/glm-5.3", roles["requirements"]["model"])
        self.assertEqual("medium", roles["requirements"]["effort"])

    def test_verifier_never_equals_producer(self):
        roles = mc.suggest(mc.usable(CATALOGUE))
        for producer, verifier in mc.INDEPENDENCE:
            self.assertNotEqual(mc.family(roles[producer]["model"]),
                                mc.family(roles[verifier]["model"]),
                                f"{producer}/{verifier}")

    def test_falls_back_within_catalogue_when_preferred_missing(self):
        roles = mc.suggest(["openai/gpt-5.6-sol", "openai/gpt-6-astra"])
        self.assertTrue(roles["builder"]["model"].startswith("openai/"))
        self.assertNotEqual(mc.family(roles["builder"]["model"]) and roles["builder"]["model"],
                            roles["validator"]["model"])
        self.assertEqual("openai/", roles["validator"]["model"][:7])


class RenderTest(unittest.TestCase):
    def test_render_lists_models_and_role_table(self):
        models = mc.usable(CATALOGUE)
        text = mc.render(models, mc.suggest(models))
        self.assertIn("xiaomi-token-plan-sgp/mimo-v2.6-pro", text)
        self.assertIn("| builder |", text)
        self.assertIn("verifier", text.lower())


if __name__ == "__main__":
    unittest.main()
