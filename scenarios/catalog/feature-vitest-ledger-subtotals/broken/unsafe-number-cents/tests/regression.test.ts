import { expect, test } from 'vitest';
import * as ledger from '../src/ledger';

test('test_c1_account_groups_keep_exact_keys_and_zero_subtotals', () => {
  expect(ledger.summarizeByAccount([
    { account: 'cash', cents: 250n },
    { account: 'Cash', cents: 10n },
    { account: '__proto__', cents: -3n },
    { account: 'cash', cents: -250n },
  ])).toEqual([
    { account: 'Cash', cents: 10n, count: 1 },
    { account: '__proto__', cents: -3n, count: 1 },
    { account: 'cash', cents: 0n, count: 2 },
  ]);
});

test('test_c2_large_cents_empty_inputs_and_input_order_are_preserved', () => {
  const entries = Object.freeze([
    Object.freeze({ account: 'z', cents: 9007199254740993n }),
    Object.freeze({ account: 'a', cents: -1n }),
    Object.freeze({ account: 'z', cents: 2n }),
  ]);
  expect(ledger.summarizeByAccount(entries)).toEqual([
    { account: 'a', cents: -1n, count: 1 },
    { account: 'z', cents: 9007199254740995n, count: 2 },
  ]);
  expect(ledger.summarizeByAccount([])).toEqual([]);
  expect(entries.map((entry) => entry.account)).toEqual(['z', 'a', 'z']);
  expect(ledger.totalCents(entries)).toBe(9007199254740994n);
});
