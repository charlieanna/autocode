/** Dollar-cost explanations from the public cost ledger; no pricing or billing guesses. */
import { money, runTotal } from "./meter";

type Row = Record<string, unknown>;
const mapping = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const amount = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const count = (value: unknown): number => Number.isInteger(value) && (value as number) > 0 ? value as number : 0;

/** Shared by the chat's keyboard-readable meter, run details and window status tooltip. */
export function costExplanation(view: unknown): string {
  const root = mapping(view);
  const cost = mapping(root.cost);
  const total = runTotal(root);
  if (!total && !Object.keys(cost).length) return "";
  const lines: string[] = [];
  if (total?.spentUsd === null || !total) lines.push("Dollar cost is unavailable. Token counts are shown independently.");
  else if (!total.costComplete) lines.push("Partial cost: the displayed amount is the known subtotal; some usage is unpriced or still in progress.");
  else lines.push("Cost coverage is complete for recorded attempts.");

  if (amount(cost.reported_usd)) lines.push(`Provider-reported: ${money(cost.reported_usd)}`);
  if (amount(cost.estimated_usd)) {
    const date = typeof cost.prices_as_of === "string" && cost.prices_as_of ? ` (prices as of ${cost.prices_as_of})` : "";
    lines.push(`Price-table estimate: ${money(cost.estimated_usd)}${date}`);
  }
  const reasons = mapping(mapping(cost.breakdown).unpriced);
  const missingUsage = count(reasons.usage_missing);
  const missingModel = count(reasons.model_unknown);
  const missingRate = count(reasons.rate_unavailable);
  const attempts = (n: number) => `${n} ${n === 1 ? "attempt" : "attempts"}`;
  if (missingUsage) lines.push(`No usable provider-reported cost for ${attempts(missingUsage)}; token usage is missing or incomplete, so it cannot be estimated.`);
  if (missingModel) lines.push(`No usable provider-reported cost for ${attempts(missingModel)}; the model identity was not recorded, so it cannot be priced.`);
  if (missingRate) {
    const models = Array.isArray(reasons.models_without_rate) ? reasons.models_without_rate.filter((value) => typeof value === "string" && value).join(", ") : "";
    lines.push(`No usable provider-reported cost for ${attempts(missingRate)}; no saved price for ${models ? `these model routes: ${models}` : "their model routes"}.`);
  }
  const explained = missingUsage + missingModel + missingRate;
  if ((count(cost.unpriced_stages) > explained || total?.spentUsd === null) && !explained) {
    lines.push("The reason for the unavailable cost was not reported by this core version.");
  } else if (count(cost.unpriced_stages) > explained) {
    lines.push(`The reason was not reported for ${attempts(count(cost.unpriced_stages) - explained)} with unavailable cost.`);
  }
  if (count(cost.unpriced_stages) || total?.spentUsd === null) lines.push("Check your provider's usage or billing page for billed charges.");
  lines.push("Estimates and provider reports are not an invoice.");
  return lines.join("\n");
}
