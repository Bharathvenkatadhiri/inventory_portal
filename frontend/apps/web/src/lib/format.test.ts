import { describe, expect, it } from "vitest";

import { ofLimit, plural, rupees } from "./format";

describe("rupees", () => {
  it("groups the Indian way and drops empty paise", () => {
    expect(rupees("150000.00")).toBe("₹1,50,000");
    expect(rupees("1250.50")).toBe("₹1,250.50");
    expect(rupees(4000)).toBe("₹4,000");
  });

  it("shows a dash for nothing", () => {
    expect(rupees(null)).toBe("—");
    expect(rupees("")).toBe("—");
  });
});

describe("counts", () => {
  it("pluralises and shows limits", () => {
    expect(plural(1, "team")).toBe("1 team");
    expect(plural(3, "team")).toBe("3 teams");
    expect(ofLimit(2, 5)).toBe("2 of 5");
    expect(ofLimit(7, null)).toBe("7 (no limit)");
  });
});
