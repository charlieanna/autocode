"""The arithmetic of a person's monthly allowance on a shared bridge (autocode_bridge_allowance)."""

import unittest
from datetime import datetime, timedelta, timezone

import autocode_bridge_allowance as allowance


def run(login, *, spent=None, reserved=None, month="2026-11", unpriced=0, complete=True, **extra):
    record = {
        "requested_by": {"login": login},
        "period": month,
        "run_dir": "/runs/x",
        "cost": {"spent_usd": spent, "unpriced_stages": unpriced, "complete": complete},
    }
    if reserved is not None:
        record["reserved_usd"] = reserved
    return {**record, **extra}


class AllowanceTests(unittest.TestCase):
    def test_caps_are_a_json_object_of_login_to_usd(self):
        self.assertEqual({}, allowance.parse_caps(""))
        self.assertEqual({"dev": 5.0, "lead": 0.0}, allowance.parse_caps('{"Dev": 5, "lead": 0}'))
        for text in ("[]", '{"dev": -1}', '{"dev": "5"}', '{"": 1}', "nope", '{"dev": 1e999}'):
            with self.assertRaises(ValueError, msg=text):
                allowance.parse_caps(text)

    def test_the_period_is_the_utc_calendar_month(self):
        late = datetime(2026, 11, 30, 23, 30, tzinfo=timezone(timedelta(hours=-5)))  # already December in UTC
        self.assertEqual("2026-12", allowance.period(late))

    def test_in_flight_runs_count_their_reservation_and_settled_runs_what_they_spent(self):
        records = [
            run("dev", spent=1.0, reserved=5.0, flying=True),
            run("dev", spent=2.5, reserved=5.0),
            run("Dev", spent=None, reserved=5.0),  # settled with unknown spend
            run("dev", spent=9.0, month="2026-10"),  # last month
            run("lead", spent=7.0),
            {**run("dev", spent=None), "queued": True},
            {**run("dev", spent=None), "failed": "refused", "run_dir": None},
        ]
        usage = allowance.used(records, "dev", "2026-11", lambda record: bool(record.get("flying")))
        self.assertEqual({"login": "dev", "period": "2026-11", "used_usd": 7.5, "runs": 3, "unknown_runs": 1}, usage)
        self.assertEqual(2.5, allowance.remaining({"dev": 10.0}, usage))
        self.assertEqual(0.0, allowance.remaining({"dev": 5.0}, usage))
        self.assertIsNone(allowance.remaining({"lead": 5.0}, usage))

    def test_a_run_that_spent_more_than_it_reserved_counts_what_it_spent(self):
        usage = allowance.used([run("dev", spent=6.0, reserved=5.0)], "dev", "2026-11", lambda record: True)
        self.assertEqual(6.0, usage["used_usd"])

    def test_the_run_cap_is_the_allowance_unless_the_options_give_a_lower_one(self):
        self.assertEqual(
            (["--engine", "codex", "--max-cost-usd", "3.5"], 3.5), allowance.capped_options(["--engine", "codex"], 3.5)
        )
        self.assertEqual((["--max-cost-usd", "3.5"], 3.5), allowance.capped_options(["--max-cost-usd", "0"], 3.5))
        self.assertEqual((["--max-cost-usd", "3.5"], 3.5), allowance.capped_options(["--max-cost-usd=20"], 3.5))
        self.assertEqual((["--max-cost-usd", "2"], 2.0), allowance.capped_options(["--max-cost-usd", "2"], 3.5))
        self.assertEqual((["--max-cost-usd", "1234567.89"], 1234567.89), allowance.capped_options([], 1234567.891))
        # Rounded down to the cent, so a reservation never exceeds what is left.
        self.assertEqual("0.01", allowance.capped_options([], 0.019)[0][-1])


if __name__ == "__main__":
    unittest.main()
