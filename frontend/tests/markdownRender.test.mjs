import assert from "node:assert/strict";
import test from "node:test";

import createDOMPurify from "dompurify";
import { JSDOM } from "jsdom";

import { renderMarkdown } from "../src/markdownRender.mjs";

const window = new JSDOM("").window;
const DOMPurify = createDOMPurify(window);
const render = (text) => renderMarkdown(text, (dirty, options) => DOMPurify.sanitize(dirty, options));

test("renderMarkdown supports GFM blocks, inline formatting, and LaTeX", () => {
  const html = render(`# Heading

**bold** and ~~removed~~ with $E = mc^2$.

| A | B |
| - | - |
| 1 | 2 |`);

  assert.match(html, /<h1>Heading<\/h1>/);
  assert.match(html, /<strong>bold<\/strong>/);
  assert.match(html, /<del>removed<\/del>/);
  assert.match(html, /class="katex"/);
  assert.match(html, /<table>/);
});

test("renderMarkdown removes executable and interactive HTML", () => {
  const html = render(`[unsafe](javascript:alert(1))

<img src="x" onerror="alert(2)">
<script>alert(3)</script>
<button onclick="alert(4)">fake action</button>`);

  assert.doesNotMatch(html, /javascript:|onerror|onclick|<script|<button/i);
});
