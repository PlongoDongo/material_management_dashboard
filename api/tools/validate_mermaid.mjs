/**
 * Prueft alle ```mermaid-Bloecke einer Markdown-Datei mit mermaids ECHTEM Parser.
 *
 *   npm install mermaid jsdom
 *   node tools/validate_mermaid.mjs ../docs/architecture.md
 *
 * Optional, aber empfehlenswert: ein Mermaid-Syntaxfehler faellt sonst erst auf,
 * wenn jemand die Datei in GitHub oder der IDE oeffnet -- und dort steht dann
 * nur "Syntax error in text" statt eines Diagramms.
 *
 * mermaid braucht ein DOM. Die Globals muessen VOR dem Import gesetzt werden,
 * weil DOMPurify sich beim Laden ans window haengt.
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
  console.error("Aufruf: node tools/validate_mermaid.mjs <datei.md>");
  process.exit(2);
}

const blocks = [...readFileSync(file, "utf8").matchAll(/```mermaid\n([\s\S]*?)```/g)].map(m => m[1]);
console.log(`${file}: ${blocks.length} Mermaid-Bloecke`);

let failed = 0;
for (const [i, code] of blocks.entries()) {
  const kind = code.trim().split("\n")[0];
  try {
    await mermaid.parse(code);
    console.log(`  [${i + 1}] OK       ${kind}`);
  } catch (e) {
    failed++;
    console.log(`  [${i + 1}] FEHLER   ${kind}`);
    console.log("      " + String(e.message).split("\n").slice(0, 8).join("\n      "));
  }
}
process.exit(failed ? 1 : 0);
