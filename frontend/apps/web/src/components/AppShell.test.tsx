import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { active } from "../test/fixtures";
import { renderApp } from "../test/render";
import { SubscriptionBanner } from "./AppShell";

describe("SubscriptionBanner", () => {
  it("says nothing while a trial has plenty of time left", async () => {
    renderApp(
      <>
        <p>The page</p>
        <SubscriptionBanner active={active({ days_left: 13 })} />
      </>,
    );
    expect(await screen.findByText("The page")).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("warns near the end of the trial and links billing managers to billing", async () => {
    renderApp(<SubscriptionBanner active={active({ days_left: 3 }, ["billing.manage"])} />);
    expect(await screen.findByRole("status")).toHaveTextContent("free trial of Starter ends on 8 Dec 2026");
    expect(screen.getByRole("link", { name: "Choose how to pay" })).toHaveAttribute("href", "/settings/billing");
  });

  it("tells others to ask an admin once the subscription has ended", async () => {
    renderApp(<SubscriptionBanner active={active({ status: "lapsed", entitled: false })} />);
    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("nothing can be changed");
    expect(banner).toHaveTextContent("Ask your Org Admin");
    expect(screen.queryByRole("link")).toBeNull();
  });
});
