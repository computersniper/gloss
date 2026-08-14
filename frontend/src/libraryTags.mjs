export function normalizeTag(tag) {
  return String(tag || "").normalize("NFKC").trim().replace(/\s+/g, " ");
}

function tagKey(tag) {
  return normalizeTag(tag).toLocaleLowerCase();
}

export function tagsMatch(left, right) {
  const leftKey = tagKey(left);
  return Boolean(leftKey) && leftKey === tagKey(right);
}

export function uniqueTags(tags) {
  const seen = new Set();
  const result = [];
  for (const rawTag of tags || []) {
    const tag = normalizeTag(rawTag);
    const key = tagKey(tag);
    if (!tag || seen.has(key)) continue;
    seen.add(key);
    result.push(tag);
  }
  return result;
}

export function mergeTags(existing, input) {
  const additions = String(input || "").split(/[,，]/);
  return uniqueTags([...(existing || []), ...additions]);
}

export function removeTag(tags, tagToRemove) {
  const removeKey = tagKey(tagToRemove);
  return uniqueTags(tags).filter((tag) => tagKey(tag) !== removeKey);
}

export function collectTags(papers) {
  return uniqueTags((papers || []).flatMap((paper) => paper.tags || []))
    .sort((left, right) => left.localeCompare(right));
}

export function filterPapersByTag(papers, activeTag) {
  const activeKey = tagKey(activeTag);
  if (!activeKey) return papers || [];
  return (papers || []).filter((paper) =>
    (paper.tags || []).some((tag) => tagKey(tag) === activeKey),
  );
}
