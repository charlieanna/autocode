import { expect, test } from 'vitest';
import { totalCents } from '../src/ledger';

test('existing_total_keeps_signed_exact_cents', () => {
  expect(totalCents([{ account: 'cash', cents: 9007199254740993n }, { account: 'fees', cents: -2n }])).toBe(9007199254740991n);
});

test('existing_empty_total_is_zero', () => {
  expect(totalCents([])).toBe(0n);
});
