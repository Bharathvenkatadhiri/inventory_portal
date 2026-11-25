import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom doesn't implement scrolling; the router restores scroll on navigation.
window.scrollTo = () => {};

afterEach(() => cleanup());
