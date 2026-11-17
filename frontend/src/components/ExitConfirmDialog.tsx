import { useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import { desktop } from "../desktop";

const EXIT_REQUEST_EVENT = "glossx:close-request";

export function ExitConfirmDialog() {
  const uiLang = useStore((state) => state.uiLang);
  const [open, setOpen] = useState(false);
  const [dontAskAgain, setDontAskAgain] = useState(false);
  const [exiting, setExiting] = useState(false);
  const cancelButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const show = () => {
      setDontAskAgain(false);
      setExiting(false);
      setOpen(true);
    };
    window.addEventListener(EXIT_REQUEST_EVENT, show);
    return () => window.removeEventListener(EXIT_REQUEST_EVENT, show);
  }, []);

  useEffect(() => {
    if (!open) return;
    cancelButton.current?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      void desktop?.cancelExit();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [open]);

  if (!open) return null;

  const cancel = async () => {
    setOpen(false);
    await desktop?.cancelExit();
  };

  const confirm = async () => {
    setExiting(true);
    try {
      await desktop?.confirmExit(dontAskAgain);
    } catch {
      setExiting(false);
    }
  };

  const zh = uiLang === "zh";
  return (
    <div className="exit-dialog-backdrop" role="presentation">
      <section className="exit-dialog" role="dialog" aria-modal="true" aria-labelledby="exit-dialog-title">
        <div className="exit-dialog-icon" aria-hidden>!</div>
        <div className="exit-dialog-content">
          <h2 id="exit-dialog-title">{zh ? "退出 GlossX？" : "Quit GlossX?"}</h2>
          <p>{zh ? "退出应用会中断尚未完成的工作：" : "Quitting will interrupt unfinished work:"}</p>
          <ul>
            <li>{zh
              ? "正在导入的任务会被取消，未完成的临时论文内容会被清理。"
              : "Active imports will be cancelled and incomplete temporary paper data will be cleaned up."}</li>
            <li>{zh
              ? "正在生成的 Summary、Mind Map、Chat 等请求会中断。"
              : "Active Summary, Mind Map, Chat, and other AI requests will be interrupted."}</li>
          </ul>
          <p className="exit-dialog-safe">{zh
            ? "已经完成并保存的论文、笔记和生成记录不会丢失。"
            : "Completed and saved papers, notes, and generated results will remain available."}</p>
          <label className="exit-dialog-checkbox">
            <input
              type="checkbox"
              checked={dontAskAgain}
              onChange={(event) => setDontAskAgain(event.target.checked)}
            />
            <span>{zh ? "下次不再提示（可在设置中重新开启）" : "Don't ask again (you can re-enable this in Settings)"}</span>
          </label>
          <div className="exit-dialog-actions">
            <button ref={cancelButton} onClick={() => void cancel()} disabled={exiting}>{zh ? "取消" : "Cancel"}</button>
            <button className="danger" onClick={() => void confirm()} disabled={exiting}>
              {exiting ? (zh ? "正在退出…" : "Quitting…") : (zh ? "退出 GlossX" : "Quit GlossX")}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
