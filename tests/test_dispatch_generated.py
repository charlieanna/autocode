"""Deterministic graph families check scheduling invariants independently of select()."""

import copy
import random
import unittest
from pathlib import PurePosixPath
from unittest.mock import patch

import autocode_dispatch as dispatch


def conflicts(left, right):
    if set(left["acceptance_criteria"]) & set(right["acceptance_criteria"]):
        return True
    for a in left["affected_paths"]:
        for b in right["affected_paths"]:
            first, second = PurePosixPath(a).parts, PurePosixPath(b).parts
            if first[: len(second)] == second or second[: len(first)] == first:
                return True
    return False


def state_for(rows, primary, limit):
    return {
        "goal_contract": {"hash": "generated-approved-plan", "body": {"milestones": rows}},
        "settings": {"orchestration": {"max_parallel": limit}},
        "current_task": {
            "kind": "implement",
            "decision": "CONTINUE",
            "milestone_id": primary["id"],
            "acceptance_criteria": list(primary["acceptance_criteria"]),
            "affected_paths": list(primary["affected_paths"]),
        },
        "milestone_progress": {},
        "builder_retries": {},
    }


class GeneratedSchedulingTests(unittest.TestCase):
    def checked_wave(self, state, accepted, eligible):
        before = copy.deepcopy(state)
        rows = state["goal_contract"]["body"]["milestones"]
        primary = next(row for row in rows if row["id"] == state["current_task"]["milestone_id"])
        limit = state["settings"]["orchestration"]["max_parallel"]
        with (
            patch.object(dispatch.goals, "execution_guard"),
            patch.object(dispatch.milestones, "accepted_ids", return_value=accepted),
        ):
            wave = dispatch.select(state)
            self.assertEqual(wave, dispatch.select(state), "The same graph must schedule deterministically")
        self.assertEqual(before, state, "Selection must not mutate the approved plan or attempt history")
        if primary["id"] not in eligible or limit == 1:
            self.assertEqual([], wave)
            return wave
        alternatives = [row for row in rows if row != primary and row["id"] in eligible]
        if not any(not conflicts(primary, row) for row in alternatives):
            self.assertEqual([], wave, "A singleton uses the serial Builder")
            return wave
        self.assertGreaterEqual(len(wave), 2, "Independent ready work must actually run in parallel")
        self.assertLessEqual(len(wave), limit)
        self.assertEqual(primary, wave[0])
        self.assertEqual(len(wave), len({row["id"] for row in wave}))
        for index, row in enumerate(wave):
            self.assertIn(row["id"], eligible)
            self.assertNotIn(row["id"], accepted)
            self.assertLessEqual(set(row["depends_on"]), accepted)
            for other in wave[index + 1 :]:
                self.assertFalse(conflicts(row, other), (row, other))
        if len(wave) < limit:
            for row in alternatives:
                if row not in wave:
                    self.assertTrue(any(conflicts(row, chosen) for chosen in wave), row)
        return wave

    def test_generated_dags_mix_ownership_dependencies_and_attempt_history(self):
        observed = set()
        for seed in range(1000):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                count = rng.randint(2, 48)
                accepted = {f"M{i}" for i in range(rng.randrange(count // 2 + 1))}
                rows, eligible, history = [], set(), {}
                for number in range(count):
                    mid = f"M{number}"
                    parents = rng.sample(range(number), rng.randint(0, min(number, 4)))
                    directory = f"part{rng.randrange(max(2, count // 3))}"
                    paths = [directory + ("/" if rng.randrange(3) == 0 else f"/file{rng.randrange(4)}.py")]
                    criteria = [f"C{rng.randrange(max(2, count // 2))}"]
                    kind = rng.choice(
                        ["fresh"] * 7 + ["acceptance_only", "attempted", "reviewed", "replanned", "failed"]
                    )
                    row = {
                        "id": mid,
                        "depends_on": [f"M{i}" for i in parents],
                        "affected_paths": paths,
                        "acceptance_criteria": criteria,
                    }
                    rows.append(row)
                    history[mid] = kind
                    observed.add(kind)
                    if (
                        mid not in accepted
                        and set(row["depends_on"]) <= accepted
                        and kind in ("fresh", "acceptance_only")
                    ):
                        eligible.add(mid)
                primary = rng.choice(rows)
                rng.shuffle(rows)
                state = state_for(rows, primary, rng.randint(1, 8))
                for row in rows:
                    mid, kind = row["id"], history[row["id"]]
                    progress = {
                        "acceptance_only": {"seconds": 7, "acceptance_test_seconds": 7},
                        "attempted": {"seconds": 8, "acceptance_test_seconds": 7},
                        "reviewed": {"reviews": 1},
                        "replanned": {"replans": 1},
                    }.get(kind, {})
                    state["milestone_progress"][f"generated-approved-plan:{mid}"] = progress
                    if kind == "failed":
                        key = dispatch.builder_policy.key(state, {"milestone_id": mid})
                        state["builder_retries"][key] = {"failures": 1}
                self.checked_wave(state, accepted, eligible)
        self.assertEqual({"fresh", "acceptance_only", "attempted", "reviewed", "replanned", "failed"}, observed)

    def test_generated_fanouts_and_joins_drain_only_after_predecessor_acceptance(self):
        parallel_waves = 0
        for seed in range(128):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                rows = []
                for number in range(rng.randint(24, 64)):
                    parents = rng.sample(range(number), rng.randint(0, min(number, 5)))
                    rows.append(
                        {
                            "id": f"M{number}",
                            "depends_on": [f"M{i}" for i in parents],
                            "affected_paths": [f"component{number}/"],
                            "acceptance_criteria": [f"C{number}"],
                        }
                    )
                rng.shuffle(rows)
                accepted = set()
                while len(accepted) < len(rows):
                    ready = [row for row in rows if row["id"] not in accepted and set(row["depends_on"]) <= accepted]
                    self.assertTrue(ready, "Every acyclic graph must make progress")
                    primary = rng.choice(ready)
                    state = state_for(rows, primary, rng.randint(2, 7))
                    wave = self.checked_wave(state, accepted, {row["id"] for row in ready})
                    parallel_waves += bool(wave)
                    accepted.update(row["id"] for row in (wave or [primary]))
                self.assertEqual({row["id"] for row in rows}, accepted)
        self.assertGreater(parallel_waves, 128, "The generated family must include repeated parallel waves")


if __name__ == "__main__":
    unittest.main()
