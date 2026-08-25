import { useEffect, useMemo, useRef } from "react";
import DOMPurify from "dompurify";
import { renderMarkdown } from "../markdownRender.mjs";

export function Markdown({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const html = useMemo(() => {
    try {
      return renderMarkdown(text, (dirty: string, options: object) => DOMPurify.sanitize(dirty, options));
    } catch {
      return null;
    }
  }, [text]);

  useEffect(() => {
    if (!ref.current) return;
    if (html === null) {
      ref.current.textContent = text || "";
      return;
    }

    ref.current.innerHTML = html;
    for (const anchor of ref.current.querySelectorAll("a[href]")) {
      const href = anchor.getAttribute("href") || "";
      if (/^https?:\/\//i.test(href)) {
        anchor.setAttribute("target", "_blank");
        anchor.setAttribute("rel", "noopener noreferrer");
      }
    }
  }, [html, text]);

  return <div className="markdown" ref={ref} />;
}
