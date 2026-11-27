import { describe, expect, it } from "vitest";

import { batchesIn, batchText, emptyBatch, picksIn, splitSerials } from "./batches";

describe("batch inputs", () => {
  it("splits scanned serial numbers on lines, spaces and commas", () => {
    expect(splitSerials("SN-1\nSN-2, SN-3;  SN-4\n\n")).toEqual(["SN-1", "SN-2", "SN-3", "SN-4"]);
  });

  it("sends only batches with a number, and serials one by one", () => {
    const rows = [{ ...emptyBatch(), number: " H-7 ", quantity: "150", supplier_batch: "HT-9" }, emptyBatch()];
    expect(batchesIn("batch", rows, "")).toEqual([{ number: "H-7", quantity: "150", supplier_batch: "HT-9", expires_on: null }]);
    expect(batchesIn("serial", rows, "A1 A2")).toEqual([{ number: "A1", supplier_batch: "" }, { number: "A2", supplier_batch: "" }]);
    expect(batchesIn("none", rows, "A1")).toEqual([]);
  });

  it("leaves picking to the server unless batches were chosen", () => {
    expect(picksIn(null)).toEqual([]);
    expect(picksIn([])).toEqual([]);
    expect(picksIn([{ batch_id: 4, quantity: "30" }])).toEqual([{ batch_id: 4, number: "", quantity: "30" }]);
  });

  it("describes what a line used", () => {
    const used = [{ batch_id: 1, number: "B-1", quantity: "40", expires_on: null }, { batch_id: 2, number: "B-2", quantity: "10", expires_on: null }];
    expect(batchText(used)).toBe("B-1 (40), B-2 (10)");
    expect(batchText(used, "serial")).toBe("B-1, B-2");
  });
});
