import { ApiError, openPdf, tab } from "@makesetu/api";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("openPdf", () => {
  let win: { close: ReturnType<typeof vi.fn>; location: { href: string } };

  beforeEach(() => {
    sessionStorage.clear();
    win = { close: vi.fn(), location: { href: "" } };
    vi.spyOn(window, "open").mockReturnValue(win as unknown as Window);
    URL.createObjectURL = vi.fn(() => "blob:pdf");
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("fetches with this tab's company and user, then shows the PDF in the new tab", async () => {
    tab.remember(7, 42);
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(new Blob(["%PDF"]), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await openPdf("/api/v1/sales-invoices/3/pdf");
    const headers = fetchMock.mock.calls[0][1]!.headers as Headers;
    expect(headers.get("X-Organization")).toBe("42");
    expect(headers.get("X-User-Id")).toBe("7");
    expect(win.location.href).toBe("blob:pdf");
    expect(win.close).not.toHaveBeenCalled();
  });

  it("closes the empty tab and reports the server's message on failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Not found" }), { status: 404 })));
    await expect(openPdf("/api/v1/sales-invoices/3/pdf")).rejects.toBeInstanceOf(ApiError);
    expect(win.close).toHaveBeenCalled();
  });
});
