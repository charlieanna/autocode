import { expect, test } from 'vitest';
import { findConflicts } from '../src/bookings';

test('hidden_half_open_truth_table_including_negative_fractional_and_empty_intervals', () => {
  const points = [-4.5, -1, 0, 0.25, 2, 7.5];
  const bookings = points.flatMap((start) => points.filter((end) => end >= start)
    .map((end) => ({ id: `${start}:${end}`, start, end })));
  for (const start of points) {
    for (const end of points.filter((value) => value >= start)) {
      const expected = bookings.filter((row) => row.start < row.end && start < end
        && Math.max(row.start, start) < Math.min(row.end, end)).map((row) => row.id);
      expect(findConflicts(bookings, { start, end }), `${start}:${end}`).toEqual(expected);
    }
  }
});

test('hidden_validation_and_order_are_preserved', () => {
  expect(findConflicts([{ id: 'z', start: -2, end: 5 }, { id: 'a', start: 0, end: 2 }],
    { start: 0.5, end: 1 })).toEqual(['z', 'a']);
  expect(() => findConflicts([{ id: 'bad', start: 5, end: 4 }], { start: 1, end: 2 })).toThrow(RangeError);
  expect(() => findConflicts([], { start: 1, end: Infinity })).toThrow(RangeError);
});
