import { expect, test } from 'vitest';
import * as ledger from '../src/ledger';

test('hidden_arbitrary_account_keys_order_count_and_bigint_exactness', () => {
  const entries = [
    { account: 'toString', cents: 10n ** 40n },
    { account: '', cents: 0n },
    { account: ' Cash', cents: -99n },
    { account: 'cash', cents: 1n },
    { account: 'cash ', cents: 2n },
    { account: 'toString', cents: -(10n ** 40n) + 1n },
    { account: 'cash', cents: -1n },
  ];
  expect(ledger.summarizeByAccount(entries)).toEqual([
    { account: '', cents: 0n, count: 1 },
    { account: ' Cash', cents: -99n, count: 1 },
    { account: 'cash', cents: 0n, count: 2 },
    { account: 'cash ', cents: 2n, count: 1 },
    { account: 'toString', cents: 1n, count: 2 },
  ]);
});

test('hidden_results_are_fresh_and_inputs_are_not_reordered', () => {
  const entries = [{ account: 'z', cents: 7n }, { account: 'a', cents: -3n }];
  const first = ledger.summarizeByAccount(entries);
  first[0].cents = 999n;
  first.push({ account: 'invented', cents: 0n, count: 0 });
  expect(ledger.summarizeByAccount(entries)).toEqual([
    { account: 'a', cents: -3n, count: 1 }, { account: 'z', cents: 7n, count: 1 },
  ]);
  expect(entries).toEqual([{ account: 'z', cents: 7n }, { account: 'a', cents: -3n }]);
});
