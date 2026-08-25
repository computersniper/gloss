import katex from "katex";
import { marked } from "marked";

marked.setOptions({ breaks: true, gfm: true });

export const markdownSanitizeOptions = {
  USE_PROFILES: { html: true, mathMl: true },
  FORBID_TAGS: [
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "form",
    "input",
    "button",
    "textarea",
    "select",
    "option",
  ],
};

/** Render GFM and LaTeX, then pass the complete result through a caller-owned sanitizer. */
export function renderMarkdown(text, sanitize) {
  const store = [];
  const stash = (tex, display) => {
    let html;
    try {
      html = katex.renderToString(tex.trim(), { displayMode: display, throwOnError: false });
    } catch {
      html = display ? `$$${tex}$$` : `$${tex}$`;
    }
    store.push(html);
    return `%%KTX${store.length - 1}%%`;
  };

  let source = text || "";
  source = source.replace(/\$\$([\s\S]+?)\$\$/g, (_match, tex) => stash(tex, true));
  source = source.replace(/\\\[([\s\S]+?)\\\]/g, (_match, tex) => stash(tex, true));
  source = source.replace(/\\\(([\s\S]+?)\\\)/g, (_match, tex) => stash(tex, false));
  source = source.replace(/\$([^$\n]+?)\$/g, (_match, tex) => stash(tex, false));

  let html = marked.parse(source);
  html = html.replace(/%%KTX(\d+)%%/g, (_match, index) => store[Number(index)] ?? "");
  return sanitize(html, markdownSanitizeOptions);
}
