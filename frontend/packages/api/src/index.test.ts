import { describe, expect, it } from "vitest";

import { ApiError, errorMessage, readCookie, unwrap } from "./index";

describe("readCookie", () => {
  it("finds one cookie among several", () => {
    expect(readCookie("csrftoken", "sessionid=abc; csrftoken=t0k%3Den; theme=dark")).toBe("t0k=en");
    expect(readCookie("missing", "a=1")).toBe("");
  });
});

describe("errorMessage", () => {
  it("uses the server's own words", () => {
    expect(errorMessage(400, { detail: "Your plan includes 3 users." })).toBe("Your plan includes 3 users.");
  });

  it("explains validation errors and common statuses", () => {
    expect(errorMessage(422, { detail: [{ loc: ["body", "email"], msg: "field required" }] })).toMatch(/aren't valid/);
    expect(errorMessage(403, undefined)).toMatch(/permission/);
    expect(errorMessage(500, "boom")).toMatch(/our side/);
  });
});

describe("unwrap", () => {
  it("returns the data of a successful response", () => {
    expect(unwrap({ data: { ok: true }, response: new Response(null, { status: 200 }) })).toEqual({ ok: true });
  });

  it("throws an ApiError with the status and message otherwise", () => {
    const call = () => unwrap({ error: { detail: "No." }, response: new Response(null, { status: 402 }) });
    expect(call).toThrow(ApiError);
    try {
      call();
    } catch (e) {
      expect((e as ApiError).status).toBe(402);
      expect((e as ApiError).message).toBe("No.");
    }
  });
});
