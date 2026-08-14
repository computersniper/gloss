import { useEffect, useState } from "react";
import { api, type DoctorReport, type DoctorStatus } from "../api/client";
import { useStore } from "../store";

const CHECK_COPY: Record<string, { en: string; zh: string }> = {
  runtime: { en: "Runtime", zh: "运行时" },
  frontend: { en: "Frontend assets", zh: "前端资源" },
  data_dir: { en: "Data directory", zh: "数据目录" },
  cache_dir: { en: "Cache directory", zh: "缓存目录" },
  database: { en: "Database", zh: "数据库" },
  disk: { en: "Disk space", zh: "磁盘空间" },
  provider: { en: "AI connection", zh: "AI 连接" },
  provider_runtime: { en: "Provider runtime", zh: "Provider 运行环境" },
  plugins: { en: "Installed plugins", zh: "已安装插件" },
};

function statusIcon(status: DoctorStatus) {
  if (status === "pass") return "✓";
  if (status === "warning") return "!";
  return "×";
}

export function DoctorDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const uiLang = useStore((state) => state.uiLang);
  const provider = useStore((state) => state.provider);
  const checkProvider = useStore((state) => state.checkProvider);
  const [report, setReport] = useState<DoctorReport | null>(null);
  const [running, setRunning] = useState(false);
  const [testingProvider, setTestingProvider] = useState(false);
  const [error, setError] = useState("");
  const T = uiLang === "zh"
    ? {
        title: "环境与自检",
        subtitle: "检查 Gloss 的本地运行环境，不会读取或发送 API 密钥。",
        run: "重新检查",
        running: "检查中…",
        test: "测试当前 AI",
        testing: "测试连接中…",
        close: "关闭",
        healthy: "环境正常",
        attention: "需要注意",
        failed: "发现问题",
        pass: "通过",
        warning: "警告",
        fail: "失败",
        version: "版本",
        runtime: "运行时",
        mode: "运行模式",
        packaged: "桌面候选包",
        source: "源码模式",
        library: "论文 / 插件",
        checked: "检查时间",
        paths: "本地路径",
        data: "数据",
        cache: "缓存",
        frontend: "前端",
        loadError: "无法完成自检",
      }
    : {
        title: "Doctor",
        subtitle: "Check the local Gloss environment without reading or sending API keys.",
        run: "Run checks again",
        running: "Checking…",
        test: "Test active AI",
        testing: "Testing connection…",
        close: "Close",
        healthy: "Environment healthy",
        attention: "Needs attention",
        failed: "Problems found",
        pass: "Passed",
        warning: "Warnings",
        fail: "Failed",
        version: "Version",
        runtime: "Runtime",
        mode: "Mode",
        packaged: "Desktop candidate",
        source: "Source checkout",
        library: "Papers / plugins",
        checked: "Checked",
        paths: "Local paths",
        data: "Data",
        cache: "Cache",
        frontend: "Frontend",
        loadError: "Doctor could not complete",
      };

  async function refresh() {
    setRunning(true);
    setError("");
    try {
      setReport(await api.runDoctor());
    } catch (cause: any) {
      setError(String(cause?.message || cause));
    } finally {
      setRunning(false);
    }
  }

  async function testActiveProvider() {
    setTestingProvider(true);
    setError("");
    try {
      await checkProvider(provider);
      await refresh();
    } finally {
      setTestingProvider(false);
    }
  }

  useEffect(() => {
    if (open) void refresh();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [open, onClose]);

  if (!open) return null;
  const overallText = report?.status === "pass"
    ? T.healthy
    : report?.status === "warning"
      ? T.attention
      : T.failed;

  return (
    <div className="doctor-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="doctor-dialog" role="dialog" aria-modal="true" aria-label={T.title}>
        <header className="doctor-head">
          <div className="doctor-symbol">＋</div>
          <div>
            <h2>{T.title}</h2>
            <p>{T.subtitle}</p>
          </div>
          <button className="doctor-close" aria-label={T.close} onClick={onClose}>×</button>
        </header>

        <div className="doctor-actions">
          <button onClick={() => void refresh()} disabled={running || testingProvider}>
            {running ? T.running : T.run}
          </button>
          <button onClick={() => void testActiveProvider()} disabled={running || testingProvider}>
            {testingProvider ? T.testing : T.test}
          </button>
          {report && (
            <span className={`doctor-overall ${report.status}`}>
              {statusIcon(report.status)} {overallText}
            </span>
          )}
        </div>

        {error && <div className="doctor-error">{T.loadError}: {error}</div>}
        {report && (
          <div className="doctor-scroll">
            <div className="doctor-summary-grid">
              <div><span>{T.version}</span><strong>Gloss {report.version}</strong></div>
              <div><span>{T.runtime}</span><strong>Python {report.system.python}</strong></div>
              <div><span>{T.mode}</span><strong>{report.system.packaged ? T.packaged : T.source}</strong></div>
              <div><span>{T.library}</span><strong>{report.system.paper_count} / {report.system.plugin_count}</strong></div>
            </div>

            <div className="doctor-totals" aria-label="Doctor result totals">
              <span className="pass">{T.pass}<b>{report.summary.passed}</b></span>
              <span className="warning">{T.warning}<b>{report.summary.warnings}</b></span>
              <span className="fail">{T.fail}<b>{report.summary.failed}</b></span>
              <time>{T.checked}: {new Date(report.checked_at * 1000).toLocaleString()}</time>
            </div>

            <div className="doctor-checks">
              {report.checks.map((check) => (
                <article className={`doctor-check ${check.status}`} key={check.id}>
                  <span className="doctor-check-icon">{statusIcon(check.status)}</span>
                  <div>
                    <strong>{CHECK_COPY[check.id]?.[uiLang] || check.id}</strong>
                    <code>{check.detail}</code>
                  </div>
                </article>
              ))}
            </div>

            <details className="doctor-paths">
              <summary>{T.paths}</summary>
              <dl>
                <dt>{T.data}</dt><dd>{report.system.data_dir}</dd>
                <dt>{T.cache}</dt><dd>{report.system.cache_dir}</dd>
                <dt>{T.frontend}</dt><dd>{report.system.frontend_dist}</dd>
              </dl>
            </details>
          </div>
        )}
      </section>
    </div>
  );
}
