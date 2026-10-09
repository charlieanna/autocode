export interface Entry {
  readonly account: string;
  readonly cents: bigint;
}

export function totalCents(entries: readonly Entry[]): bigint {
  return entries.reduce((total, entry) => total + entry.cents, 0n);
}

export interface AccountSummary {
  account: string;
  cents: bigint;
  count: number;
}

export function summarizeByAccount(entries: readonly Entry[]): AccountSummary[] {
  const summaries = new Map<string, AccountSummary>();
  for (const entry of entries) {
    const summary = summaries.get(entry.account);
    if (summary) {
      summary.cents = BigInt(Number(summary.cents) + Number(entry.cents));
      summary.count += 1;
    } else {
      summaries.set(entry.account, { account: entry.account, cents: BigInt(Number(entry.cents)), count: 1 });
    }
  }
  return [...summaries.values()].sort((left, right) => left.account < right.account ? -1 : left.account > right.account ? 1 : 0);
}
