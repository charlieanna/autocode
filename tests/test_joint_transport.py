"""Transport refusal order and provider isolation during joint planning."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_joint_transport as transport
from autocode_util import Paused


class JointTransportTests(unittest.TestCase):
    def check(self, engines, *, changed=None, subscription_error=None, retired=None):
        events = []
        roles = {engine: {"engine": engine} for engine in engines}
        identities = {engine: f"saved-{engine}" for engine in engines}
        if retired:
            identities[retired] = f"saved-{retired}"
        state = {"settings": {"roles": roles, "transport_identities": identities}}
        workspace = Path("/project")

        def local(engine, *args):
            self.assertEqual(() if engine == "codex" else (workspace,), args)
            events.append(f"{engine}.settings")
            return f"current-{engine}"

        def drift(engine, current, saved, *selected):
            self.assertEqual(f"current-{engine}", current)
            self.assertEqual(f"saved-{engine}", saved)
            self.assertEqual(({"codex": roles["codex"]},) if engine == "codex" else (), selected)
            events.append(f"{engine}.drift")
            return engine == changed

        def codex_subscription(current):
            self.assertEqual("current-codex", current)
            events.append("codex.subscription")

        def opencode_subscription(selected, root):
            self.assertEqual({"opencode": roles["opencode"]}, selected)
            self.assertEqual(workspace, root)
            events.append("opencode.subscription")
            if subscription_error:
                raise subscription_error

        support = SimpleNamespace(
            Paused=Paused, local_settings=lambda: local("codex"), transport_drift=lambda *args: drift("codex", *args)
        )
        opencode = SimpleNamespace(
            check_subscription_routes=opencode_subscription,
            local_settings=lambda *args: local("opencode", *args),
            transport_drift=lambda *args: drift("opencode", *args),
        )
        self.events = events
        return transport.check(
            state,
            workspace,
            engine_for=lambda settings, role: roles[role]["engine"],
            support=support,
            opencode=opencode,
            check_subscription=codex_subscription,
        )

    def test_all_selected_transports_are_checked_even_after_earlier_drift(self):
        expected = [
            "codex.settings",
            "codex.subscription",
            "codex.drift",
            "opencode.subscription",
            "opencode.settings",
            "opencode.drift",
        ]
        for changed in (None, "codex", "opencode"):
            with self.subTest(changed=changed):
                if changed:
                    with self.assertRaises(Paused) as caught:
                        self.check(("codex", "opencode"), changed=changed)
                    self.assertEqual("PAUSED_TRANSPORT_CHANGED", caught.exception.status)
                    self.assertEqual("A joint-planning CLI/auth/provider configuration changed", str(caught.exception))
                else:
                    self.check(("codex", "opencode"))
                self.assertEqual(expected, self.events)

    def test_a_single_engine_does_not_check_an_unselected_provider(self):
        self.check(("codex",))
        self.assertEqual(["codex.settings", "codex.subscription", "codex.drift"], self.events)

    def test_subscription_refusal_preserves_billing_cause_and_stops_later_checks(self):
        error = RuntimeError("subscription route unavailable")
        with self.assertRaises(Paused) as caught:
            self.check(("opencode",), subscription_error=error)
        self.assertEqual("PAUSED_BILLING_ROUTE", caught.exception.status)
        self.assertIs(error, caught.exception.__cause__)
        self.assertEqual(str(error), str(caught.exception))
        self.assertEqual(["opencode.subscription"], self.events)

    def test_retired_transport_refuses_before_calling_any_provider(self):
        for retired in ("gocode", "qwen"):
            with self.subTest(retired=retired), self.assertRaises(Paused) as caught:
                self.check(("codex", "opencode"), retired=retired)
            self.assertEqual("PAUSED_TRANSPORT_CHANGED", caught.exception.status)
            self.assertEqual(
                f"This saved run used the {retired} engine, which this checkout no longer "
                "bundles; resume it from a checkout that has it, or start a new run with "
                f"--provider {retired} and a user-level provider config",
                str(caught.exception),
            )
            self.assertEqual([], self.events)

    def test_controller_passes_current_selected_provider_and_collaborators(self):
        import autocode as runner

        selected = object()
        state, workspace = {"settings": {}}, Path("/project")
        with patch.object(runner, "opencode", selected), patch.object(transport, "check") as check:
            runner.check_joint_transports(state, workspace)
        check.assert_called_once_with(
            state,
            workspace,
            engine_for=runner.planning.engine_for,
            support=runner.support,
            opencode=selected,
            check_subscription=runner.check_subscription,
        )
