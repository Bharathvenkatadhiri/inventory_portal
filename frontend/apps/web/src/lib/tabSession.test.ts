import { api, SESSION_CHANNEL, tab, watchSessionInOtherTabs } from "@makesetu/api";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Each tab says who and which organization it's showing; the server refuses
// requests that no longer match the shared session (core/api.py).

// jsdom can't build a Request from a relative URL; the app itself uses "".
const BASE = "http://localhost";

function stubLocation() {
  const reload = vi.fn();
  const assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, reload, assign, pathname: "/app/partners", search: "" });
  return { reload, assign };
}

describe("tab session", () => {
  beforeEach(() => sessionStorage.clear());
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("sends the tab's user and organization with every request", async () => {
    const fetchMock = vi.fn<(request: Request) => Promise<Response>>(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
    tab.remember(7, 42);
    await api.GET("/api/v1/me", { baseUrl: BASE, fetch: fetchMock });
    const request = fetchMock.mock.calls[0][0];
    expect(request.headers.get("X-User-Id")).toBe("7");
    expect(request.headers.get("X-Organization")).toBe("42");
  });

  it("sends neither before the tab has loaded who it is", async () => {
    const fetchMock = vi.fn<(request: Request) => Promise<Response>>(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
    await api.GET("/api/v1/me", { baseUrl: BASE, fetch: fetchMock });
    const request = fetchMock.mock.calls[0][0];
    expect(request.headers.has("X-User-Id")).toBe(false);
    expect(request.headers.has("X-Organization")).toBe(false);
  });

  it("reloads with an explanation when the server says the session changed", async () => {
    const { reload } = stubLocation();
    tab.remember(7, 42);
    const conflict = { detail: "You signed in as a different person in another tab.", code: "user" };
    const fetchMock = vi.fn<(request: Request) => Promise<Response>>(async () => new Response(JSON.stringify(conflict), { status: 409, headers: { "Content-Type": "application/json" } }));
    await api.GET("/api/v1/me", { baseUrl: BASE, fetch: fetchMock });
    expect(reload).toHaveBeenCalled();
    expect(tab.user()).toBe("");
    expect(tab.organization()).toBe("");
    expect(tab.takeNotice()).toMatch(/different person/);
    expect(tab.takeNotice()).toBe(""); // shown once
  });

  it("leaves other 409s to the page", async () => {
    const { reload } = stubLocation();
    const fetchMock = vi.fn<(request: Request) => Promise<Response>>(async () => new Response(JSON.stringify({ detail: "Already exists." }), { status: 409, headers: { "Content-Type": "application/json" } }));
    await api.GET("/api/v1/me", { baseUrl: BASE, fetch: fetchMock });
    expect(reload).not.toHaveBeenCalled();
  });

  it("follows sign-outs and other people's sign-ins from other tabs", async () => {
    const { reload, assign } = stubLocation();
    tab.remember(7, 42);
    const stop = watchSessionInOtherTabs();
    const other = new BroadcastChannel(SESSION_CHANNEL);
    const delivered = () => new Promise((resolve) => setTimeout(resolve, 20));

    other.postMessage({ user: 7 }); // same person: nothing to do
    await delivered();
    expect(reload).not.toHaveBeenCalled();
    expect(assign).not.toHaveBeenCalled();

    other.postMessage({ user: 9 }); // someone else signed in
    await delivered();
    expect(reload).toHaveBeenCalledTimes(1);

    tab.remember(7, 42);
    other.postMessage({ user: null }); // signed out
    await delivered();
    expect(assign).toHaveBeenCalledWith("/login/?next=%2Fapp%2Fpartners");
    expect(tab.user()).toBe("");

    other.close();
    stop();
  });
});
