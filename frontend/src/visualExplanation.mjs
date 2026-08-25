export function wrapFrame(index, count) {
  if (!Number.isInteger(count) || count <= 0) return 0;
  return ((index % count) + count) % count;
}

export function shouldAutoplay(style, count, reducedMotion) {
  return style === "animated" && count > 1 && !reducedMotion;
}
