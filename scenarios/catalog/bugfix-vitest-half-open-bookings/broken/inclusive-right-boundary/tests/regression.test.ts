import { expect, test } from 'vitest';
import { findConflicts } from '../src/bookings';

test('test_c1_touching_bookings_do_not_conflict', () => {
  const bookings = [
    { id: 'left', start: 10, end: 20 },
    { id: 'inside', start: 20, end: 30 },
    { id: 'right', start: 30, end: 40 },
  ];
  expect(findConflicts(bookings, { start: 20, end: 30 })).toEqual(['inside']);
  expect(findConflicts(bookings, { start: 19.5, end: 20.5 })).toEqual(['left', 'inside']);
});

test('test_c2_empty_intervals_do_not_conflict_or_mutate_inputs', () => {
  const bookings = Object.freeze([
    Object.freeze({ id: 'positive', start: 10, end: 20 }),
    Object.freeze({ id: 'empty', start: 15, end: 15 }),
  ]);
  const requested = Object.freeze({ start: 15, end: 15 });
  expect(findConflicts(bookings, requested)).toEqual([]);
  expect(findConflicts(bookings, { start: 14, end: 16 })).toEqual(['positive']);
  expect(bookings).toEqual([{ id: 'positive', start: 10, end: 20 }, { id: 'empty', start: 15, end: 15 }]);
  expect(requested).toEqual({ start: 15, end: 15 });
});
