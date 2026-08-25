import { useEffect, useMemo, useState } from "react";
import type { VisualExplanation as VisualExplanationData } from "../api/client";
import { shouldAutoplay, wrapFrame } from "../visualExplanation.mjs";

export function VisualExplanation({
  visual,
  uiLang,
}: {
  visual: VisualExplanationData;
  uiLang: "en" | "zh";
}) {
  const images = visual.images || [];
  const [index, setIndex] = useState(0);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const firstUrl = images[0]?.url || "";

  useEffect(() => {
    const query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const sync = () => setReducedMotion(Boolean(query?.matches));
    sync();
    query?.addEventListener?.("change", sync);
    return () => query?.removeEventListener?.("change", sync);
  }, []);

  useEffect(() => {
    setIndex(0);
    setFailedUrl(null);
    setPlaying(shouldAutoplay(visual.style, images.length, reducedMotion));
  }, [firstUrl, visual.style, images.length, reducedMotion]);

  useEffect(() => {
    if (!playing || !shouldAutoplay(visual.style, images.length, reducedMotion)) return;
    const timer = window.setInterval(() => {
      setIndex((current) => wrapFrame(current + 1, images.length));
    }, 4200);
    return () => window.clearInterval(timer);
  }, [playing, visual.style, images.length, reducedMotion]);

  const image = images[wrapFrame(index, images.length)];
  const labels = useMemo(() => uiLang === "zh"
    ? {
        model: "真实图像生成", frame: "分镜", previous: "上一帧", next: "下一帧",
        play: "播放", pause: "暂停", download: "下载图像", open: "打开原图",
        failed: "图像加载失败。可尝试重新运行插件。",
      }
    : {
        model: "Real image generation", frame: "Frame", previous: "Previous", next: "Next",
        play: "Play", pause: "Pause", download: "Download image", open: "Open full image",
        failed: "The image could not be loaded. Try running the plugin again.",
      }, [uiLang]);

  if (!image) return null;
  const isAnimated = visual.style === "animated" && images.length > 1;
  const failed = failedUrl === image.url;
  const backend = visual.image_provider === "codex"
    ? "Codex image_gen"
    : `OpenAI · ${visual.model}`;

  return (
    <section className="visual-explanation" aria-label={labels.model}>
      <header className="visual-explanation-meta">
        <span>{labels.model}</span>
        <code>{backend}</code>
        <span>{visual.quality}</span>
      </header>
      <figure>
        {failed
          ? <div className="visual-image-error" role="alert">{labels.failed}</div>
          : <img
              src={image.url}
              alt={image.alt}
              loading="lazy"
              onError={() => setFailedUrl(image.url)}
            />}
        <figcaption>
          <strong>{image.heading}</strong>
          <span>{image.caption}</span>
        </figcaption>
      </figure>
      {isAnimated && <div className="visual-frame-controls" aria-label={labels.frame}>
        <button type="button" onClick={() => setIndex((current) => wrapFrame(current - 1, images.length))}>
          ← {labels.previous}
        </button>
        <button
          type="button"
          onClick={() => setPlaying((current) => !current)}
          disabled={reducedMotion}
          title={reducedMotion ? "prefers-reduced-motion" : undefined}
        >
          {playing ? `Ⅱ ${labels.pause}` : `▶ ${labels.play}`}
        </button>
        <span aria-live="polite">{labels.frame} {index + 1} / {images.length}</span>
        <button type="button" onClick={() => setIndex((current) => wrapFrame(current + 1, images.length))}>
          {labels.next} →
        </button>
      </div>}
      <div className="visual-image-actions">
        <a href={image.url} download>{labels.download}</a>
        <a href={image.url} target="_blank" rel="noreferrer">{labels.open}</a>
      </div>
    </section>
  );
}
