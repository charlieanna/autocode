import copy
import itertools
import random
import unittest

from intervalset import coalesce


def graph_oracle(intervals):
    # Independent definition: connected components of positive-overlap edges.
    remaining = set(range(len(intervals)))
    result = []
    while remaining:
        group = {remaining.pop()}
        changed = True
        while changed:
            changed = False
            for j in list(remaining):
                if any(max(intervals[i][0], intervals[j][0]) < min(intervals[i][1], intervals[j][1]) for i in group):
                    group.add(j)
                    remaining.remove(j)
                    changed = True
        result.append((min(intervals[i][0] for i in group), max(intervals[i][1] for i in group)))
    return sorted(result)


class HiddenIntervalTests(unittest.TestCase):
    def test_nested_transitive_repeated_and_touching(self):
        cases = [
            [(1, 10), (2, 3), (9, 12)],
            [(1, 3), (3, 5)],
            [(2, 4), (2, 4)],
            [(5, 8), (0, 2), (1, 6)],
            [(-9, -4), (-4, 0), (0, 2)],
            [],
        ]
        for values in cases:
            for ordering in list(itertools.permutations(values))[:12]:
                self.assertEqual(coalesce(iter(ordering)), graph_oracle(values))

    def test_generated_overlap_graphs(self):
        rng = random.Random(5801)
        for _ in range(180):
            intervals = []
            for _ in range(rng.randrange(1, 12)):
                start = rng.randrange(-12, 12)
                intervals.append([start, start + rng.randrange(1, 8)])
            before = copy.deepcopy(intervals)
            result = coalesce(intervals)
            self.assertEqual(result, graph_oracle(intervals))
            self.assertEqual(intervals, before)
            self.assertTrue(all(isinstance(item, tuple) for item in result))

    def test_invalid_intervals(self):
        for bad in [[(1, 1)], [(4, 1)], [(True, 3)], [(1, 2.0)], [("1", 3)], [(1,)], [(1, 2, 3)], [None]]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                coalesce(bad)
