import { pdfjsLib } from "./pdfSetup";
import { api } from "../api/client";

// Keep a few parsed PDF documents alive so switching between open paper tabs
// does not re-download and re-parse the file. Least-recently-used is evicted.
const MAX_DOCS = 4;
const docs = new Map<string, Promise<any>>();

export function getPdfDoc(id: string): Promise<any> {
  let doc = docs.get(id);
  if (doc) {
    docs.delete(id);
    docs.set(id, doc);
    return doc;
  }
  doc = pdfjsLib.getDocument(api.pdfUrl(id)).promise;
  docs.set(id, doc);
  doc.catch(() => {
    // A closed tab may already have started a new load for the same paper.
    if (docs.get(id) === doc) docs.delete(id);
  });
  while (docs.size > MAX_DOCS) {
    const oldest = docs.keys().next().value as string;
    evictPdfDoc(oldest);
  }
  return doc;
}

export function evictPdfDoc(id: string) {
  const doc = docs.get(id);
  if (!doc) return;
  docs.delete(id);
  void doc.then((d) => {
    try {
      d.destroy();
    } catch {
      /* already destroyed */
    }
  }).catch(() => {});
}
