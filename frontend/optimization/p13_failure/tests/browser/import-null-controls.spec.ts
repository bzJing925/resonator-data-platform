import { expect, test } from "@playwright/test";
import type { BrowserData } from "../../src/types.js";
import { browserPaths, readViewerData } from "../helpers.js";

const { indexUrl, validText } = browserPaths();

async function importPayload(page: import("@playwright/test").Page, text: string): Promise<void> {
  await page.getByLabel("导入 browser_data.json").setInputFiles({
    name: "browser_data.json",
    mimeType: "application/json",
    buffer: Buffer.from(text),
  });
}

test.beforeEach(async ({ page }) => {
  await page.goto(indexUrl);
  await expect(page.locator("[data-candidate-row]")).toHaveCount(20);
});

test("A03-BROWSER-CONTROL-001 valid current candidate import keeps the complete report", async ({ page }) => {
  await importPayload(page, validText);
  await expect(page.locator("#import-message")).toContainText("导入校验通过");
  await expect(page.locator("[data-candidate-row]")).toHaveCount(20);
  await expect(page.locator("#metric-rows [data-metric-row]")).toHaveCount(160);
  await expect(page.locator("#c19-evidence [data-metric-row]")).toHaveCount(8);
});

test("A03-BROWSER-CONTROL-002 forged grade import is rejected without replacing current state", async ({ page }) => {
  const payload = readViewerData() as BrowserData;
  Reflect.set(payload.candidates[0]!, "grade", "A");
  const before = await page.locator("[data-candidate-row]").first().textContent();
  await importPayload(page, JSON.stringify(payload));
  await expect(page.locator("#import-message")).toContainText("已拒绝导入");
  await expect(page.locator("#import-message")).toContainText("candidates.0.grade");
  await expect(page.locator("[data-candidate-row]").first()).toHaveText(before ?? "");
  await expect(page.locator("[data-candidate-row]")).toHaveCount(20);
});
