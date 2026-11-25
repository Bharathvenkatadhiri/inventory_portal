import { useEffect, useId, useRef, type ReactNode } from "react";

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl font-extrabold tracking-tight text-navy-900">{title}</h1>
        {description ? <p className="mt-1 max-w-2xl text-sm text-gray-500">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}

export function Card({ title, description, actions, children, className = "" }: {
  title?: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {title || actions ? (
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            {title ? <h2 className="text-base font-bold text-navy-900">{title}</h2> : null}
            {description ? <p className="mt-0.5 text-sm text-gray-500">{description}</p> : null}
          </div>
          {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

const TONES = {
  gray: "bg-gray-100 text-gray-700",
  green: "bg-green-50 text-green-700 ring-1 ring-inset ring-green-200",
  amber: "bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200",
  red: "bg-red-50 text-red-700 ring-1 ring-inset ring-red-200",
  navy: "bg-navy-50 text-navy-800 ring-1 ring-inset ring-navy-100",
  orange: "bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent-200",
} as const;

export function Badge({ tone = "gray", children }: { tone?: keyof typeof TONES; children: ReactNode }) {
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${TONES[tone]}`}>{children}</span>;
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
      {message}
    </p>
  );
}

export function Notice({ tone = "navy", children }: { tone?: "navy" | "amber" | "red" | "green"; children: ReactNode }) {
  const styles = {
    navy: "border-navy-100 bg-white text-navy-900",
    amber: "border-amber-200 bg-amber-50 text-amber-900",
    red: "border-red-200 bg-red-50 text-red-800",
    green: "border-green-200 bg-green-50 text-green-800",
  }[tone];
  return <div className={`rounded-xl border px-4 py-3 text-sm ${styles}`}>{children}</div>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="py-8 text-center text-sm text-gray-500" role="status">
      {label}
    </p>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-gray-300 px-4 py-8 text-center">
      <p className="text-sm font-semibold text-navy-900">{title}</p>
      {children ? <div className="mt-1 text-sm text-gray-500">{children}</div> : null}
    </div>
  );
}

type FieldProps = {
  label: string;
  hint?: ReactNode;
  children: (id: string) => ReactNode;
};

/** A label, the control (given the id to use), and an optional hint under it. */
export function Field({ label, hint, children }: FieldProps) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="field-label">
        {label}
      </label>
      {children(id)}
      {hint ? <p className="mt-1 text-xs text-gray-500">{hint}</p> : null}
    </div>
  );
}

export function Toggle({ label, description, checked, onChange, disabled }: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <label htmlFor={id} className="text-sm font-semibold text-navy-900">
          {label}
        </label>
        {description ? <p className="text-sm text-gray-500">{description}</p> : null}
      </div>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 disabled:opacity-50 ${checked ? "bg-accent-500" : "bg-gray-300"}`}
      >
        <span className={`my-1 h-4 w-4 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-6" : "translate-x-1"}`} />
      </button>
    </div>
  );
}

/** A dialog over the page. Escape or the close button dismisses it. */
export function Modal({ title, onClose, children, footer, wide = false }: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  // Kept in a ref so a new onClose on each render (an inline arrow) doesn't
  // re-run the effect and pull focus back to the first field while typing.
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && close.current();
    document.addEventListener("keydown", onKey);
    const first = panel.current?.querySelector<HTMLElement>("input, select, textarea")
      ?? panel.current?.querySelector<HTMLElement>("[data-autofocus], button");
    first?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-navy-950/40 px-4 py-10">
      <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId}
        className={`w-full rounded-xl bg-white shadow-xl ${wide ? "max-w-2xl" : "max-w-lg"}`}>
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-4">
          <h2 id={titleId} className="text-base font-bold text-navy-900">{title}</h2>
          <button type="button" onClick={onClose} className="rounded p-1 text-gray-400 hover:text-navy-900" aria-label="Close">
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        <div className="space-y-4 px-5 py-5">{children}</div>
        {footer ? <div className="flex justify-end gap-2 border-t border-gray-100 px-5 py-4">{footer}</div> : null}
      </div>
    </div>
  );
}

/** A usage bar: how much of a plan limit is used. No limit → no bar. */
export function Meter({ label, used, limit, note }: { label: string; used: number; limit: number | null | undefined; note?: ReactNode }) {
  const unlimited = limit === null || limit === undefined;
  const share = unlimited || limit === 0 ? 0 : Math.min(1, used / limit);
  const full = !unlimited && used >= (limit ?? 0);
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span className="font-semibold text-navy-900">{label}</span>
        <span className="tabular-nums text-gray-600">{unlimited ? `${used}, no limit` : `${used} of ${limit}`}</span>
      </div>
      {!unlimited ? (
        <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-gray-100" aria-hidden="true">
          <div className={`h-full rounded-full ${full ? "bg-amber-500" : "bg-navy-600"}`} style={{ width: `${share * 100}%` }} />
        </div>
      ) : null}
      {note ? <p className="mt-1 text-xs text-gray-500">{note}</p> : null}
    </div>
  );
}
