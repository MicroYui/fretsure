/**
 * Capture the README screenshots from a running, offline fretsure-serve.
 *
 * Everything here uses the bundled CC0 example and the default deterministic
 * engine, so a capture run makes no model call and needs no network. Start the
 * server first:
 *
 *   uv run fretsure-serve
 *   npm run screenshots
 */

import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium, type Page } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(HERE, "../../docs/assets/readme");
const BASE_URL = process.env.FRETSURE_BASE_URL ?? "http://127.0.0.1:8000";
const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  // Let the serif display face and the fretboard SVG paint before capturing.
  await page.waitForTimeout(400);
}

async function arrangeExample(page: Page): Promise<void> {
  await page.getByText("Oracle ready").waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: /load the CC0 example/i }).click();
  await page.getByRole("button", { name: /Arrange and verify/i }).click();
  await page.getByRole("heading", { name: /inside the hand model/i }).waitFor({
    timeout: 180_000,
  });
  await settle(page);
}

async function main(): Promise<void> {
  await mkdir(OUT_DIR, { recursive: true });
  const browser = await chromium.launch();
  try {
    const desktop = await browser.newContext({
      viewport: DESKTOP,
      deviceScaleFactor: 2,
      colorScheme: "dark",
      reducedMotion: "reduce",
    });
    const page = await desktop.newPage();

    await page.goto(BASE_URL);
    await settle(page);
    await page.screenshot({ path: `${OUT_DIR}/landing.png` });

    await arrangeExample(page);
    // The workspace top already frames player, notation, fretboard and verdict.
    await page.screenshot({ path: `${OUT_DIR}/workspace.png` });

    const verdict = page.locator(".p6-oracle-card").first();
    if (await verdict.count()) {
      await verdict.scrollIntoViewIfNeeded();
      await settle(page);
      await verdict.screenshot({ path: `${OUT_DIR}/evidence.png` });
    }

    const trace = page
      .locator("section")
      .filter({ has: page.getByRole("heading", { name: /What changed, and why/i }) })
      .last();
    if (await trace.count()) {
      await trace.scrollIntoViewIfNeeded();
      await settle(page);
      await trace.screenshot({ path: `${OUT_DIR}/trace.png` });
    }
    await desktop.close();

    const phone = await browser.newContext({
      viewport: MOBILE,
      deviceScaleFactor: 3,
      isMobile: true,
      hasTouch: true,
      colorScheme: "dark",
      reducedMotion: "reduce",
    });
    const small = await phone.newPage();
    await small.goto(BASE_URL);
    await settle(small);
    await arrangeExample(small);
    await small.screenshot({ path: `${OUT_DIR}/mobile.png` });
    await phone.close();
  } finally {
    await browser.close();
  }
  console.log(`screenshots written to ${OUT_DIR}`);
}

await main();
