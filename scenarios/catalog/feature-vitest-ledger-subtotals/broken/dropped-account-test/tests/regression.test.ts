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
