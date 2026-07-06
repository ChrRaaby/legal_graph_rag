import { test, expect } from "@playwright/test";

// E1+E2 acceptance smoke: mount the real UI, run a real agent question, then
// exercise every lens (Kredsløb scrub, Graflinse + inspector, Tankestrøm
// drill-down) and the chat trust features (kilder chips, feedback).
// Requires the server on :8000 serving the build.
test("live run, lenses, kilder, feedback", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/");

  // React mounted + runtime-truth architecture fetched.
  await expect(page.locator(".brand")).toContainText("Skattegraf");
  await expect(page.locator(".badge", { hasText: "love" })).toBeVisible();

  // A question that reliably retrieves + cites provisions.
  await page.locator(".composer textarea").fill("Hvad er reglerne for beskatning af gevinst på aktier?");
  await page.locator(".composer button.send").click();

  await expect(page.locator(".thinking-line")).toBeVisible();
  await expect(page.locator(".msg.agent").last()).toContainText(/aktie|§/i, { timeout: 80_000 });

  // Timeline populated; scrub to the start updates the caption.
  await expect(page.locator(".tl-track svg .sp").first()).toBeVisible();
  const scrub = page.locator("input.scrub");
  await expect(scrub).toBeEnabled();
  await scrub.fill("0");
  await expect(page.locator(".caption")).toContainText(/tænker|planl|Kalder/i);

  // Kilder chips rendered under the answer, with verification.
  await expect(page.locator(".kilde").first()).toBeVisible();

  // Feedback registers.
  await page.locator(".fb", { hasText: "👍" }).click();
  await expect(page.locator(".fb-thanks")).toBeVisible();

  // Clicking a verified kilde chip jumps to Graflinse and opens the inspector.
  await page.locator(".kilde:not([disabled])").first().click();
  await expect(page.locator('.tab[aria-selected="true"]')).toContainText("Graflinse");
  await expect(page.locator(".node-panel")).toBeVisible();
  await expect(page.locator(".node-panel h4")).toContainText("§");

  // Graflinse rendered graph nodes (retrieved subgraph).
  expect(await page.locator("#graflinse .gn, .layer .gn").count()).toBeGreaterThan(1);

  // Tankestrøm: reasoning/tool cards + I/O drill-down present.
  await page.locator(".tab", { hasText: "Tankestrøm" }).click();
  await expect(page.locator(".th").first()).toBeVisible();
  await expect(page.locator(".th .meta").first()).toBeVisible();
  await expect(page.locator(".th details summary").first()).toBeVisible();

  expect(errors, "no uncaught page errors").toEqual([]);
});
