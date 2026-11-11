import type { BrowserData, ImportResult } from "./types.js";
import { parseBrowserDataJson } from "./validation.js";

export function importBrowserDataJson(current: BrowserData, text: string): ImportResult {
  const validation = parseBrowserDataJson(text);
  return validation.ok
    ? { accepted: true, data: validation.data, validation }
    : { accepted: false, data: current, validation };
}
