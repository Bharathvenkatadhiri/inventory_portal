import { openPdf } from "@makesetu/api";
import { useState } from "react";

/** Opens a document's PDF (an /api/v1/.../pdf URL) in a new tab. */
export function PdfButton({ url, label = "PDF", className = "btn-secondary" }: { url: string; label?: string; className?: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <span className="inline-flex flex-col">
      <button
        type="button"
        className={className}
        disabled={busy}
        onClick={async (e) => {
          e.stopPropagation();
          setBusy(true);
          setError("");
          try {
            await openPdf(url);
          } catch (err) {
            setError(err instanceof Error ? err.message : "Couldn't open the PDF.");
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Opening…" : label}
      </button>
      {error ? <span className="mt-1 text-xs text-red-600">{error}</span> : null}
    </span>
  );
}
