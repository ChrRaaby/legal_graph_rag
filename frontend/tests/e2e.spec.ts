import { test, expect } from "@playwright/test";

// E1 acceptance smoke: mount the real UI, run a real agent question, watch it
// live, then scrub the replay. Requires the server on :8000 serving the build.
test("live run then scrub", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/");

  // React mounted + runtime-truth architecture fetched (the graph-stats badge).
  await expect(page.locator(".brand")).toContainText("Skattegraf");
  await expect(page.locator(".badge", { hasText: "love" })).toBeVisible();

  // Ask a short question and send.
  await page.locator(".composer textarea").fill("Hvad er momssatsen i Danmark?");
  await page.locator(".composer button.send").click();

  // Live: thinking indicator appears.
  await expect(page.locator(".thinking-line")).toBeVisible();

  // Answer arrives (SSE round-trip rendered).
  await expect(page.locator(".msg.agent").last()).toContainText("%", { timeout: 80_000 });

  // Timeline populated with spans after the run.
  await expect(page.locator(".tl-track svg .sp").first()).toBeVisible();

  // Scrub to the start and assert the caption reflects an earlier moment.
  const scrub = page.locator("input.scrub");
  await expect(scrub).toBeEnabled();
  await scrub.fill("0");
  await expect(page.locator(".caption")).toContainText(/tænker|planl|Kalder/i);

  // The circuit rendered nodes (runtime-truth map).
  expect(await page.locator("#root svg .nd").count()).toBeGreaterThan(4);

  expect(errors, "no uncaught page errors").toEqual([]);
});
