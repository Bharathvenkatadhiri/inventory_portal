import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Meter } from "./ui";

describe("Meter", () => {
  it("shows usage against the plan limit", () => {
    render(<Meter label="Users" used={3} limit={3} note="All in use" />);
    expect(screen.getByText("3 of 3")).toBeInTheDocument();
    expect(screen.getByText("All in use")).toBeInTheDocument();
  });

  it("has no bar when there's no limit", () => {
    const { container } = render(<Meter label="Teams" used={7} limit={null} />);
    expect(screen.getByText("7, no limit")).toBeInTheDocument();
    expect(container.querySelector("[aria-hidden='true']")).toBeNull();
  });
});
