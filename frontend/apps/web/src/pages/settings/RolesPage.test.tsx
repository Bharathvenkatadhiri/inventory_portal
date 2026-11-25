import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { permissions } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { RoleEditor } from "./RolesPage";

describe("RoleEditor", () => {
  it("lets a light role take only shop-floor permissions", async () => {
    renderApp(<RoleEditor role={null} permissions={permissions} onClose={() => {}} />);
    const purchase = await screen.findByRole("checkbox", { name: /Create purchase orders/ });
    await userEvent.click(purchase);
    expect(purchase).toBeChecked();

    await userEvent.selectOptions(screen.getByLabelText("Who it's for"), "light");
    expect(purchase).toBeDisabled();
    expect(purchase).not.toBeChecked(); // dropped when the role became light
    expect(screen.getByRole("checkbox", { name: /Post goods receipts/ })).toBeEnabled();
    expect(screen.getAllByText("(full users only)")).toHaveLength(2);
  });

  it("marks permissions the plan doesn't include", async () => {
    renderApp(<RoleEditor role={null} permissions={permissions} onClose={() => {}} />);
    expect(await screen.findByText("Growth")).toBeInTheDocument();
  });
});
