import { expect, test } from "@playwright/test";
import { browserPaths } from "../helpers.js";

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
});

test("offline report shows failure decision, all rows, controls, metrics and C19 separation", async ({ page }) => {
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("当前证据不支持 k² 提升 1 个百分点");
  await expect(page.locator("[data-candidate-row]")).toHaveCount(20);
  await expect(page.locator('[data-control-type="negative_control"]')).toHaveCount(3);
  await expect(page.locator("#metric-rows [data-metric-row]")).toHaveCount(160);
  await expect(page.locator("#c19-evidence [data-metric-row]")).toHaveCount(8);
  await expect(page.locator("#current-decision")).toContainText("REJECTED");
  await expect(page.locator("#current-decision")).toContainText("无当前推荐");
  await expect(page.locator("#current-decision")).toContainText("grade: null");
  await expect(page.locator("#c19-evidence")).toContainText("s_c454ac7ec8a2a7f8");
  await expect(page.locator("#c19-evidence")).toContainText("942.3123232596486");
  await expect(page.locator("#c19-evidence")).toContainText("历史模型预测");
  await expect(page.locator("#process-status")).toContainText("not_executed");
});

test("desktop and narrow layouts retain landmarks without page-level horizontal overflow", async ({ page }) => {
  await expect(page.getByRole("main")).toBeVisible();
  await expect(page.getByRole("table", { name: "20 个历史对照候选" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const layout = await page.evaluate(() => {
    const documentElement = document.documentElement;
    return {
      overflow: documentElement.scrollWidth > documentElement.clientWidth,
      scrollRegionsContained: [...document.querySelectorAll<HTMLElement>(".table-wrap")].every((region) => {
        const rect = region.getBoundingClientRect();
        return rect.left >= 0 && rect.right <= documentElement.clientWidth;
      }),
    };
  });
  expect(layout.overflow).toBe(false);
  expect(layout.scrollRegionsContained).toBe(true);
  const metricRegion = page.getByRole("region", { name: "指标表，可横向滚动" });
  const locallyScrollable = await metricRegion.evaluate((region) => {
    region.scrollLeft = region.scrollWidth;
    return region.scrollWidth > region.clientWidth && region.scrollLeft > 0;
  });
  expect(locallyScrollable).toBe(true);
});

test("no external requests are made", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (request) => {
    if (/^https?:/.test(request.url())) external.push(request.url());
  });
  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  expect(external).toEqual([]);
});

for (const kind of ["candidate-id", "grade", "source-hash"] as const) {
  test(`rejects tampered ${kind} import without replacing valid state`, async ({ page }) => {
    const before = await page.locator("[data-candidate-row]").first().textContent();
    const payload = JSON.parse(validText) as {
      candidates: Array<{ candidate_id: string; grade: unknown; source_sha256: string }>;
    };
    if (kind === "candidate-id") payload.candidates[0]!.candidate_id = "tampered";
    if (kind === "grade") payload.candidates[0]!.grade = "A";
    if (kind === "source-hash") payload.candidates[0]!.source_sha256 = "0".repeat(64);
    await importPayload(page, JSON.stringify(payload));
    await expect(page.getByRole("alert")).toContainText("已拒绝导入");
    await expect(page.locator("[data-candidate-row]").first()).toHaveText(before ?? "");
    await expect(page.locator("[data-candidate-row]")).toHaveCount(20);
  });
}

test("valid JSON import is accepted and keeps the complete report", async ({ page }) => {
  await importPayload(page, validText);
  await expect(page.getByRole("status")).toContainText("导入校验通过");
  await expect(page.locator("[data-candidate-row]")).toHaveCount(20);
  await expect(page.locator("#metric-rows [data-metric-row]")).toHaveCount(160);
  await expect(page.locator("#c19-evidence [data-metric-row]")).toHaveCount(8);
});
