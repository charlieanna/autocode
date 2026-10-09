import { expect, test } from 'vitest';
import { findConflicts } from '../src/bookings';

test('existing_overlap_returns_ids_in_input_order', () => {
  expect(findConflicts([
    { id: 'later', start: 14, end: 19 },
    { id: 'earlier', start: 10, end: 20 },
    { id: 'outside', start: 22, end: 25 },
  ], { start: 15, end: 17 })).toEqual(['later', 'earlier']);
});

test('existing_invalid_intervals_are_rejected', () => {
  expect(() => findConflicts([], { start: 20, end: 10 })).toThrow(RangeError);
  expect(() => findConflicts([], { start: Number.NaN, end: 10 })).toThrow(RangeError);
  expect(() => findConflicts([{ id: 'invalid', start: 1, end: Infinity }], { start: 2, end: 3 })).toThrow(RangeError);
});
