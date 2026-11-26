/** Rupees as people in India write them: ₹1,50,000, with paise only when there are some. */
export function rupees(amount: string | number | null | undefined): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  const value = typeof amount === "number" ? amount : Number(amount);
  if (Number.isNaN(value)) return "—";
  const whole = Number.isInteger(value);
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: whole ? 0 : 2,
  }).format(value);
}

export function day(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" }).format(new Date(iso));
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** "3 of 10", or "3 (no limit)" when the plan has none. */
export function ofLimit(used: number, limit: number | null | undefined): string {
  return limit === null || limit === undefined ? `${used} (no limit)` : `${used} of ${limit}`;
}

/** A stock quantity in Indian grouping (1,25,000.5), up to 3 decimals, with its unit. */
export function quantity(value: string | number | null | undefined, unit = ""): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return "—";
  const text = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 3 }).format(n);
  return unit ? `${text} ${unit}` : text;
}
