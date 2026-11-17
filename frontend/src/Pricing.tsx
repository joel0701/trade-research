/** Cost icons for connections and data sources: free, free with an account, or paid plan. */
export const PRICING: { value: string; label: string; hint: string }[] = [
  { value: "free", label: "Free", hint: "Free, no account needed (e.g. Yahoo Finance, DuckDuckGo)" },
  { value: "account", label: "Free with account", hint: "Free, but needs an account (e.g. a brokerage)" },
  { value: "paid", label: "Paid plan", hint: "Needs a paid plan or usage billing (e.g. Massive, Bigdata.com)" },
];

const svg = { width: 14, height: 14, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
              strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

const ICONS: Record<string, JSX.Element> = {
  // A dollar sign struck through: no cost.
  free: <svg {...svg}><circle cx="12" cy="12" r="9.5" /><path d="M14.5 9.2c-.4-.9-1.4-1.5-2.5-1.5-1.5 0-2.6.8-2.6 2s1.1 1.7 2.6 2 2.6.8 2.6 2-1.1 2-2.6 2c-1.2 0-2.2-.6-2.6-1.5M12 6v1.7M12 16.3V18M5.5 18.5l13-13" /></svg>,
  // A person: free once you have an account.
  account: <svg {...svg}><circle cx="12" cy="8" r="3.5" /><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5" /></svg>,
  // A dollar sign: paid plan.
  paid: <svg {...svg}><circle cx="12" cy="12" r="9.5" /><path d="M14.5 9.2c-.4-.9-1.4-1.5-2.5-1.5-1.5 0-2.6.8-2.6 2s1.1 1.7 2.6 2 2.6.8 2.6 2-1.1 2-2.6 2c-1.2 0-2.2-.6-2.6-1.5M12 6v1.7M12 16.3V18" /></svg>,
};

export function PricingIcon({ pricing }: { pricing?: string | null }) {
  const p = PRICING.find((x) => x.value === pricing);
  if (!p) return null;
  return <span className={`pricing pricing-${p.value}`} title={p.hint} aria-label={p.label} role="img">{ICONS[p.value]}</span>;
}

export function PricingLegend() {
  return (
    <p className="muted small pricing-legend">
      {PRICING.map((p) => <span key={p.value}><PricingIcon pricing={p.value} /> {p.label}</span>)}
    </p>
  );
}
