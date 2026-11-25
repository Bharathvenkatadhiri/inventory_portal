/**
 * The typed client for MakeSetu's /api/v1 (Django Ninja, core/api.py).
 *
 * `schema.ts` is generated from `openapi.json` (`pnpm generate-api`), which is
 * exported from Django (`manage.py export_openapi_schema`), so a request or
 * response that doesn't match the server fails to type-check.
 *
 * The app is served by Django on the same origin, so it signs in with the
 * Django session cookie; changes carry the CSRF token from the `csrftoken`
 * cookie, as Django requires.
 */
import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

export type { components, paths };
export type Schemas = components["schemas"];

/** A request the server refused, with its own explanation. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export function readCookie(name: string, cookies: string = document.cookie): string {
  for (const part of cookies.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return "";
}

export function csrfToken(): string {
  return readCookie("csrftoken");
}

/** Where a signed-out request sends the person: sign in, then come back. */
export function signInUrl(next: string = window.location.pathname + window.location.search): string {
  return `/login/?next=${encodeURIComponent(next)}`;
}

const csrf: Middleware = {
  onRequest({ request }) {
    if (!SAFE_METHODS.has(request.method)) {
      request.headers.set("X-CSRFToken", csrfToken());
    }
    return request;
  },
};

/**
 * What this tab is showing: who it was loaded for and which organization.
 * Every tab shares one Django session, so without this a tab left open on
 * one organization would save into whichever organization another tab
 * switched to (and act as whoever signed in last). Kept in sessionStorage,
 * which belongs to one tab; the server checks both on every request
 * (core/api.py, organizations/workspace.py).
 */
const TAB_USER = "makesetu.tab.user";
const TAB_ORGANIZATION = "makesetu.tab.organization";
const TAB_NOTICE = "makesetu.tab.notice";

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null; // storage blocked: requests go without the headers and use the session's organization
  }
}

export const tab = {
  user: () => storage()?.getItem(TAB_USER) ?? "",
  organization: () => storage()?.getItem(TAB_ORGANIZATION) ?? "",
  /** Remembers who and which organization the tab is showing (after /me loads, or a switch). */
  remember(userId: number, organizationId: number | null | undefined) {
    const s = storage();
    if (!s) return;
    s.setItem(TAB_USER, String(userId));
    if (organizationId) s.setItem(TAB_ORGANIZATION, String(organizationId));
    else s.removeItem(TAB_ORGANIZATION);
  },
  forget() {
    storage()?.removeItem(TAB_USER);
    storage()?.removeItem(TAB_ORGANIZATION);
  },
  /** A message to show once after the tab reloads, explaining why it did. */
  takeNotice(): string {
    const s = storage();
    const notice = s?.getItem(TAB_NOTICE) ?? "";
    s?.removeItem(TAB_NOTICE);
    return notice;
  },
  reload(notice: string) {
    tab.forget();
    if (notice) storage()?.setItem(TAB_NOTICE, notice);
    window.location.reload();
  },
};

const tabHeaders: Middleware = {
  onRequest({ request }) {
    const user = tab.user();
    const organization = tab.organization();
    if (user) request.headers.set("X-User-Id", user);
    if (organization) request.headers.set("X-Organization", organization);
    return request;
  },
};

/** The server's 409 codes for "this tab no longer matches the session" (core/api_errors.py). */
export const SESSION_CONFLICT_CODES = new Set(["user", "organization", "session"]);

const sessionChanged: Middleware = {
  async onResponse({ response }) {
    if (response.status === 401) {
      tab.forget();
      window.location.assign(signInUrl());
    } else if (response.status === 409) {
      const body = await response.clone().json().catch(() => null);
      if (body && SESSION_CONFLICT_CODES.has(body.code)) {
        tab.reload(typeof body.detail === "string" ? body.detail : "");
      }
    }
    return response;
  },
};

/**
 * Tabs tell each other when the session changes. Django pages announce who
 * they were rendered for (templates/_session_broadcast.html); a page
 * rendered signed out means every tab is signed out, since they share the
 * cookie. Any other tab of the app then reacts at once instead of on its
 * next request.
 */
export const SESSION_CHANNEL = "makesetu-session";

/** Tells the other tabs who this tab is signed in as (the app's own page isn't a Django template). */
export function announceSession(userId: number) {
  if (typeof BroadcastChannel === "undefined") return;
  try {
    const channel = new BroadcastChannel(SESSION_CHANNEL);
    channel.postMessage({ user: userId });
    channel.close();
  } catch {
    // not worth breaking the app over
  }
}

export function watchSessionInOtherTabs(): () => void {
  if (typeof BroadcastChannel === "undefined") return () => {};
  const channel = new BroadcastChannel(SESSION_CHANNEL);
  channel.onmessage = (event: MessageEvent) => {
    const data = event.data as { user?: number | null } | null;
    if (!data || !("user" in data)) return;
    const mine = tab.user();
    if (data.user === null) {
      tab.forget();
      window.location.assign(signInUrl());
    } else if (mine && String(data.user) !== mine) {
      tab.reload("You signed in as a different person in another tab, so this tab has reloaded.");
    }
  };
  return () => channel.close();
}

export const api = createClient<paths>({ baseUrl: "", credentials: "same-origin" });
api.use(csrf, tabHeaders, sessionChanged);

/** The server's message for a failed request, in words a person can act on. */
export function errorMessage(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      return "Some of the details aren't valid. Check the form and try again.";
    }
  }
  if (status === 403) return "You don't have permission to do that.";
  if (status === 404) return "That couldn't be found. It may have been removed.";
  if (status >= 500) return "Something went wrong on our side. Please try again.";
  return "That didn't work. Please try again.";
}

/**
 * The data of an openapi-fetch result, or an ApiError. Lets callers (and
 * TanStack Query) use the success value directly and catch one error type.
 */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    throw new ApiError(result.response.status, errorMessage(result.response.status, result.error));
  }
  return result.data as T;
}
