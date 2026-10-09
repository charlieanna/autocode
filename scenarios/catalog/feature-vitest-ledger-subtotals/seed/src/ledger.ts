export interface Entry {
  readonly account: string;
  readonly cents: bigint;
}

export function totalCents(entries: readonly Entry[]): bigint {
  return entries.reduce((total, entry) => total + entry.cents, 0n);
}
