"""Keyboard liveness, bounded reads and cancellation without clocks that wait."""

import json
import unittest
from unittest import mock

import autocode_chat_client as chat
import autocode_chat_polling as polling

from tests.test_chat_client import DONE, RUN, WORKSPACE, ScriptedChat
from tests.test_chat_tui import screen_ui


class Child:
    def __init__(self, output, number):
        self.output, self.pid, self.code = output, 10000 + number, None
        self.reaped = False

    def poll(self):
        return self.code

    def finish(self, view):
        self.output.write(json.dumps({"view": view}).encode())
        self.code = 0

    def wait(self):
        self.reaped = True
        return self.code


class Reads(unittest.TestCase):
    def setUp(self):
        self.now, self.children = 100.0, []

        def launch(argv, **kw):
            child = Child(kw["stdout"], len(self.children))
            child.argv = argv
            self.children.append(child)
            return child

        self.poller = polling.StatusPoller(("autocode",), clock=lambda: self.now, launch=launch)
        self.kill = mock.patch.object(polling.os, "killpg").start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(self.poller.close)
        self.keys = {(f"/p/{n}", f"/p/{n}/run"): 0 for n in range(25)}

    def test_no_queue_and_fairness_even_when_every_read_hangs(self):
        admitted = set()
        for _ in range(14):
            results = self.poller.tick(self.keys)
            self.assertLessEqual(len(self.poller.active), 2)
            admitted.update(self.poller.active)
            self.assertTrue(all("timed out" in error for _, _, error in results))
            self.now += polling.READ_TIMEOUT
        self.assertEqual(set(self.keys), admitted)
        self.assertTrue(all(child.reaped for child in self.children[:-2]))

    def test_healthy_chat_updates_while_another_read_hangs(self):
        keys = dict(list(self.keys.items())[:2])
        self.poller.tick(keys)
        self.children[1].finish({"done": True})
        results = self.poller.tick(keys)
        self.assertEqual([(list(keys)[1], json.dumps({"view": {"done": True}}), "")], results)
        self.assertFalse(self.children[0].reaped)

    def test_refresh_cancels_an_actual_outstanding_old_request(self):
        key = next(iter(self.keys))
        self.poller.tick({key: 0})
        old = self.children[-1]
        old.finish({"needs": {"kind": "approve_plan", "token": "OLD"}})
        self.poller.refresh(key)  # response ready but not yet consumed
        self.assertTrue(old.reaped)
        self.assertTrue(old.output.closed)
        self.assertEqual([], self.poller.tick({key: 0}))
        self.children[-1].finish({"needs": {"kind": "approve_plan", "token": "NEW"}})
        result = self.poller.tick({key: 100})
        self.assertIn("NEW", result[0][1])
        self.assertNotIn("OLD", result[0][1])

    def test_removal_and_close_reap_all_inflight_reads(self):
        self.poller.tick(self.keys)
        first = self.children[0]
        self.poller.tick(dict(list(self.keys.items())[1:]))
        self.assertTrue(first.reaped)
        self.poller.close()
        self.assertTrue(all(child.reaped and child.output.closed for child in self.children))
        self.assertEqual([], self.poller.tick(self.keys))


class AttachedInput(unittest.TestCase):
    def test_attached_wait_consumes_keyboard_and_observes_other_chats(self):
        client = ScriptedChat({})
        working = {"liveness": {"kind": "supervised"}, "needs": {"kind": "continue"}}
        views = iter((working, DONE))
        client.status = lambda: next(views)
        client._wait = mock.Mock(return_value="pause")
        with mock.patch.object(client, "request_pause") as pause, mock.patch.object(client, "check_others") as others:
            client.watch()
        client._wait.assert_called_once()
        pause.assert_called_once()
        others.assert_called_once()
        self.assertEqual([], client.taken, "viewing never launches a second controller")

    def test_repeated_attached_ctrl_c_during_pause_never_signals_worker(self):
        client = ScriptedChat({})
        client.status = mock.Mock(side_effect=[KeyboardInterrupt, DONE])
        with (
            mock.patch.object(client, "request_pause", side_effect=KeyboardInterrupt),
            mock.patch.object(chat.os, "killpg") as kill,
        ):
            client.watch()
        kill.assert_not_called()
        self.assertIn("external worker is unchanged", client.text())

    def test_attached_pause_guidance_and_no_pause_offer(self):
        client = ScriptedChat({})
        client._attached = True
        client.request_pause()
        self.assertIn("only request a boundary pause", client.text())
        self.assertNotIn("interrupt the current step", client.text())

    def test_status_errors_are_visible_and_do_not_fabricate_completion(self):
        client = chat.ChatClient(WORKSPACE, RUN, say=lambda _: None)
        self.addCleanup(client.close)
        key = (str(WORKSPACE), str(RUN))
        client._poller.tick = mock.Mock(return_value=[(key, "", "timed out")])
        client.poll()
        self.assertEqual("unavailable", client.tabs()[0]["state"])
        self.assertIsNone(client._status_view)
        client._poller.tick.return_value = [(key, json.dumps({"view": DONE}), "")]
        client.poll()
        self.assertEqual("done", client.tabs()[0]["state"])

    def test_response_from_another_run_cannot_change_current_plan(self):
        client = chat.ChatClient(WORKSPACE, RUN, say=lambda _: None)
        self.addCleanup(client.close)
        key = (str(WORKSPACE), str(RUN))
        client._poller.tick = mock.Mock(return_value=[(key, json.dumps({"view": {"run_dir": "/other/run"}}), "")])
        client.poll()
        self.assertIsNone(client._status_view)
        self.assertEqual(RUN, client.run_dir)

    def test_question_keeps_draft_and_pumps_observation_between_keys(self):
        ui = screen_ui("h", "i", "\n")
        ui.idle = mock.Mock()
        self.assertEqual("hi", ui.ask("Your answer?"))
        self.assertGreaterEqual(ui.idle.call_count, 3)

    def test_static_screen_has_no_ticker_redraw_but_working_marker_animates(self):
        ui = screen_ui()
        ui._stopping.wait = mock.Mock(side_effect=[False, True])
        with mock.patch.object(ui, "_draw") as draw:
            ui._tick()
        draw.assert_not_called()
        ui._launched = True
        ui._stopping.wait.side_effect = [False, True]
        with mock.patch.object(ui, "_draw") as draw:
            ui._tick()
        draw.assert_called_once()
