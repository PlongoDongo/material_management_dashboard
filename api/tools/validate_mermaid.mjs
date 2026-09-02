/**
 * Validates every ```mermaid block in a Markdown file with mermaid's REAL parser.
 *
 *   npm install mermaid jsdom
 *   node tools/validate_mermaid.mjs ../docs/architecture.md
 *
 * Optional but recommended: otherwise a mermaid syntax error only shows up when
 * somebody opens the file in GitHub or an IDE -- and all they see there is
 * "Syntax error in text" instead of a diagram.
 *
 * mermaid needs a DOM. The globals must be set BEFORE the import, because
 * DOMPurify attaches itself to window while loading.
 */
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><body></body>", { pretendToBeVisual: true });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
global.Element = dom.window.Element;
global.HTMLElement = dom.window.HTMLElement;
global.SVGElement = dom.window.SVGElement;
global.DOMParser = dom.window.DOMParser;
global.Node = dom.window.Node;

const mermaid = (await import("mermaid")).default;
mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });

const file = process.argv[2];
if (!file) {
  console.error("Usage: node tools/validate_mermaid.mjs <file.md>");
  process.exit(2);
}

const blocks = [...readFileSync(file, "utf8").matchAll(/```mermaid\n([\s\S]*?)```/g)].map(m => m[1]);
console.log(`${file}: ${blocks.length} mermaid blocks`);

let failed = 0;
for (const [i, code] of blocks.entries()) {
  const kind = code.trim().split("\n")[0];
  try {
    await mermaid.parse(code);
    console.log(`  [${i + 1}] OK       ${kind}`);
  } catch (e) {
    failed++;
    console.log(`  [${i + 1}] ERROR    ${kind}`);
    console.log("      " + String(e.message).split("\n").slice(0, 8).join("\n      "));
  }
}
process.exit(failed ? 1 : 0);
