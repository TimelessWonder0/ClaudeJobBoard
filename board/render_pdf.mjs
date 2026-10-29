// HTML -> PDF with the Playwright Chromium that career-ops already installed.
//   node render_pdf.mjs <career-ops-dir> <input.html> <output.pdf>
// US Letter, text stays selectable (ATS parsers read it as text).
import { createRequire } from 'node:module';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [, , careerOpsDir, input, output] = process.argv;
if (!careerOpsDir || !input || !output) {
  console.error('usage: node render_pdf.mjs <career-ops-dir> <input.html> <output.pdf>');
  process.exit(2);
}
const require = createRequire(path.join(path.resolve(careerOpsDir), 'package.json'));
const { chromium } = require('playwright');

const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.goto(pathToFileURL(path.resolve(input)).href, { waitUntil: 'load' });
  await page.pdf({
    path: path.resolve(output),
    format: 'Letter',
    printBackground: false,
    margin: { top: '0in', right: '0in', bottom: '0in', left: '0in' }, // margins come from the HTML @page rule
    preferCSSPageSize: true,
  });
} finally {
  await browser.close();
}
