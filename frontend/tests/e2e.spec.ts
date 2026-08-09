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

  // Eval lens opens on the Testsuite sub-tab; Historik holds the run views.
  await page.locator(".tab", { hasText: "Eval" }).click();
  await expect(page.locator(".subtab", { hasText: "Testsuite" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".dimtable.golden")).toBeVisible();
  // The run selector belongs to Historik, so it must NOT be on the suite tab.
  await expect(page.locator(".eval-selects")).toHaveCount(0);

  await page.locator(".subtab", { hasText: "Historik" }).click();
  await expect(page.locator(".eval-selects select").first()).toBeVisible();
  await expect(page.locator(".dimtable .etbl tbody tr").first()).toBeVisible();
  await expect(page.locator(".etbl.items .item-row").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Værktøjs-sundhed")).toBeVisible();
  // Cost is shown alongside tokens on the run tiles.
  await expect(page.locator(".tiles")).toContainText(/tokens|forbrug/);
  // The golden browser lives on the suite tab only.
  await expect(page.locator(".dimtable.golden")).toHaveCount(0);

  // ── E4: golden-set browser (back on Testsuite) ───────────────────────────
  await page.locator(".subtab", { hasText: "Testsuite" }).click();
  const golden = page.locator(".dimtable.golden");
  await expect(golden).toBeVisible();
  await expect(golden.locator("h5")).toContainText("Golden set");
  const allRows = await golden.locator(".etbl.items .item-row").count();
  expect(allRows).toBeGreaterThan(20); // whole set is browsable, not just run items

  // Free-text search narrows the list.
  await golden.locator(".golden-filters input").fill("dagpenge");
  await expect(golden.locator("h5")).toContainText("1/", { timeout: 10_000 });
  await expect(golden.locator(".etbl.items .item-row")).toHaveCount(1);
  await golden.locator("button.ghost", { hasText: "Ryd" }).click();

  // Tag facet filter (the F1 guardrail items).
  await golden.locator(".golden-filters select").first().selectOption("tags");
  await golden.locator(".golden-filters select").nth(1).selectOption("f1_gate");
  await expect(golden.locator(".etbl.items .item-row")).toHaveCount(19, { timeout: 10_000 });

  // Item detail exposes the facit + the must_contain terms.
  await golden.locator(".etbl.items .item-row td").nth(1).click();
  await expect(golden.locator(".item-detail")).toBeVisible();
  await expect(golden.locator(".item-detail .gtag").first()).toBeVisible();

  // Runner is gated behind a selection and capped at 5.
  const runBtn = golden.locator("button.run");
  await expect(runBtn).toBeDisabled();
  await golden.locator('.etbl.items input[type="checkbox"]').first().check();
  await expect(runBtn).toBeEnabled();

  // Run one gated item end-to-end: it must come back with a shield badge and pass.
  await runBtn.click();
  await expect(page.locator(".verdict").first()).toBeVisible({ timeout: 90_000 });
  await expect(page.locator(".verdict").first()).toHaveClass(/ok/);
  await expect(page.locator(".verdict .gatebadge").first()).toBeVisible();
  // Tokens + cost ride along with the verdict (a gated item costs one
  // classifier call, so "lokal" or a kr. figure — never a blank).
  await expect(page.locator(".verdict .vhead")).toContainText(/tok|forbrug/);

  expect(errors, "no uncaught page errors").toEqual([]);
});
