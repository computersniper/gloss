import type { PluginConfigurationProperty, PluginManifest } from "../api/client";
import { useStore } from "../store";

interface FeatureDescriptor {
  id: string;
  name: string;
  nameZh: string;
  icon: string;
  description: string;
  plugin?: PluginManifest;
}

const CORE_FEATURES: FeatureDescriptor[] = [
  { id: "core.summary", name: "Summary", nameZh: "总结", icon: "🛰️", description: "Paper summary and key findings" },
  { id: "core.translate", name: "Translate", nameZh: "翻译", icon: "🌐", description: "Selection and paper translation" },
  { id: "core.notes", name: "Deep Paper Note", nameZh: "深度论文笔记", icon: "📚", description: "Structured long-form reading note" },
  { id: "core.mindmap", name: "Concept Map", nameZh: "概念图", icon: "🗺️", description: "Paper concept and relationship map" },
  { id: "core.chat", name: "Chat", nameZh: "论文对话", icon: "💬", description: "Questions and follow-up conversations" },
  { id: "core.explain", name: "Explain", nameZh: "解释", icon: "💡", description: "Explain selected academic content" },
];

interface Props {
  cfg: any;
  providers: string[];
  saving: boolean;
  setCfg: React.Dispatch<React.SetStateAction<any>>;
  onSave: () => void;
}

function SchemaField({
  settingKey,
  schema,
  value,
  uiLang,
  onChange,
}: {
  settingKey: string;
  schema: PluginConfigurationProperty;
  value: unknown;
  uiLang: "en" | "zh";
  onChange: (value: unknown) => void;
}) {
  const label = uiLang === "zh"
    ? schema.title_zh || schema.title || settingKey
    : schema.title || settingKey;
  const description = uiLang === "zh"
    ? schema.description_zh || schema.description
    : schema.description;
  const current = value ?? schema.default ?? (schema.type === "boolean" ? false : "");
  let control: React.ReactNode;

  if (schema.type === "boolean") {
    control = (
      <label className="setting-toggle feature-toggle">
        <input type="checkbox" checked={Boolean(current)} onChange={(event) => onChange(event.target.checked)} />
        <span>{description || label}</span>
      </label>
    );
  } else if (schema.enum?.length) {
    control = (
      <select value={String(current)} onChange={(event) => {
        const match = schema.enum!.find((option) => String(option) === event.target.value);
        onChange(match ?? event.target.value);
      }}>
        {schema.enum.map((option, index) => (
          <option key={String(option)} value={String(option)}>
            {(uiLang === "zh" ? schema.enumItemLabels_zh?.[index] : undefined)
              || schema.enumItemLabels?.[index]
              || schema.enumDescriptions?.[index]
              || String(option)}
          </option>
        ))}
      </select>
    );
  } else if (schema.type === "number" || schema.type === "integer") {
    control = (
      <input
        type="number"
        value={String(current)}
        min={schema.minimum}
        max={schema.maximum}
        step={schema.type === "integer" ? 1 : "any"}
        onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))}
      />
    );
  } else if (schema.type === "array" && (!schema.items?.type || schema.items.type === "string")) {
    control = (
      <textarea
        rows={3}
        value={Array.isArray(current) ? current.join("\n") : String(current)}
        placeholder={uiLang === "zh" ? "每行一个值" : "One value per line"}
        onChange={(event) => onChange(event.target.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean))}
      />
    );
  } else {
    control = <input value={String(current)} onChange={(event) => onChange(event.target.value)} />;
  }

  return (
    <div className="feature-setting-row">
      {schema.type !== "boolean" && <label htmlFor={`feature-${settingKey}`}>{label}</label>}
      {control}
      {schema.type !== "boolean" && description && <p>{description}</p>}
      <code>{settingKey}</code>
    </div>
  );
}

export function FeatureSettingsEditor({ cfg, providers, saving, setCfg, onSave }: Props) {
  const uiLang = useStore((state) => state.uiLang);
  const focus = useStore((state) => state.settingsFocus);
  const openFeatureSettings = useStore((state) => state.openFeatureSettings);
  const installed = useStore((state) => state.pluginSnapshot.installed);
  const pluginFeatures: FeatureDescriptor[] = installed.map((plugin) => ({
    id: `plugin:${plugin.id}`,
    name: plugin.name,
    nameZh: plugin.name_zh || plugin.name,
    icon: plugin.icon || "🧩",
    description: plugin.description,
    plugin,
  }));
  const features = [...CORE_FEATURES, ...pluginFeatures];
  const selected = features.find((feature) => feature.id === focus) || features[0];
  const settings = cfg.feature_settings?.[selected.id] || {};
  const specific = settings.configuration || {};
  const properties = Object.entries(selected.plugin?.contributes?.configuration?.properties || {})
    .sort(([a, left], [b, right]) => ((left.order ?? 1_000) - (right.order ?? 1_000)) || a.localeCompare(b));

  const patch = (next: Record<string, unknown>) => {
    setCfg((current: any) => ({
      ...current,
      feature_settings: {
        ...(current.feature_settings || {}),
        [selected.id]: {
          provider: "",
          model: "",
          effort: "",
          context_mode: "full",
          configuration: {},
          ...(current.feature_settings?.[selected.id] || {}),
          ...next,
        },
      },
    }));
  };
  const setSpecific = (key: string, value: unknown) => patch({
    configuration: { ...specific, [key]: value },
  });
  const text = uiLang === "zh"
    ? {
        general: "常规设置", heading: "插件与功能", core: "内置功能", installed: "已安装插件",
        provider: "AI Provider", global: "使用全局 Provider", model: "模型", modelHint: "留空时使用 Provider 默认模型",
        effort: "推理强度", context: "论文上下文", full: "每次发送完整论文", shared: "省 Token：复用论文会话",
        custom: "插件专属设置", noCustom: "此功能没有额外配置。通用 AI 设置仍会独立保存。",
        save: "保存此功能设置", hostManaged: "这些通用设置由 Gloss 自动提供；插件作者无需重复声明。",
      }
    : {
        general: "General settings", heading: "Plugins & features", core: "Built-in features", installed: "Installed plugins",
        provider: "AI provider", global: "Use global provider", model: "Model", modelHint: "Leave blank to use the provider default",
        effort: "Reasoning effort", context: "Paper context", full: "Send the full paper for each task", shared: "Save tokens: reuse the paper session",
        custom: "Extension settings", noCustom: "This feature has no additional settings. Its common AI settings are still saved independently.",
        save: "Save feature settings", hostManaged: "Gloss supplies these common settings automatically; extension authors do not need to declare them.",
      };

  return (
    <div className="feature-settings-workbench">
      <aside className="feature-settings-nav">
        <button className="settings-general-link" onClick={() => openFeatureSettings(null)}>← {text.general}</button>
        <h4>{text.heading}</h4>
        <span>{text.core}</span>
        {CORE_FEATURES.map((feature) => (
          <button key={feature.id} className={selected.id === feature.id ? "active" : ""} onClick={() => openFeatureSettings(feature.id)}>
            <i>{feature.icon}</i>{uiLang === "zh" ? feature.nameZh : feature.name}
          </button>
        ))}
        {pluginFeatures.length > 0 && <span>{text.installed}</span>}
        {pluginFeatures.map((feature) => (
          <button key={feature.id} className={selected.id === feature.id ? "active" : ""} onClick={() => openFeatureSettings(feature.id)}>
            <i>{feature.icon}</i>{uiLang === "zh" ? feature.nameZh : feature.name}
          </button>
        ))}
      </aside>
      <main className="feature-settings-main">
        <header>
          <div className="feature-settings-icon">{selected.icon}</div>
          <div>
            <h3>{uiLang === "zh" ? selected.nameZh : selected.name}</h3>
            <p>{selected.description}</p>
            <code>{selected.id}</code>
          </div>
        </header>
        <p className="host-settings-note">◆ {text.hostManaged}</p>
        <section>
          <div className="feature-setting-row">
            <label>{text.provider}</label>
            <select value={settings.provider || ""} onChange={(event) => patch({ provider: event.target.value })}>
              <option value="">{text.global}</option>
              {providers.map((provider) => <option key={provider} value={provider}>{provider}</option>)}
            </select>
          </div>
          <div className="feature-setting-row">
            <label>{text.model}</label>
            <input value={settings.model || ""} placeholder={text.modelHint} onChange={(event) => patch({ model: event.target.value })} />
          </div>
          <div className="feature-setting-row">
            <label>{text.effort}</label>
            <select value={settings.effort || ""} onChange={(event) => patch({ effort: event.target.value })}>
              <option value="">{uiLang === "zh" ? "默认" : "Default"}</option>
              {["minimal", "low", "medium", "high", "xhigh", "max"].map((effort) => <option key={effort}>{effort}</option>)}
            </select>
          </div>
          <div className="feature-setting-row">
            <label>{text.context}</label>
            <select value={settings.context_mode || "full"} onChange={(event) => patch({ context_mode: event.target.value })}>
              <option value="full">{text.full}</option>
              <option value="shared_session">{text.shared}</option>
            </select>
          </div>
        </section>
        <section className="feature-specific-settings">
          <h4>{(uiLang === "zh"
            ? selected.plugin?.contributes?.configuration?.title_zh
            : selected.plugin?.contributes?.configuration?.title) || text.custom}</h4>
          {properties.length ? properties.map(([key, schema]) => (
            <SchemaField
              key={key}
              settingKey={key}
              schema={schema}
              value={specific[key]}
              uiLang={uiLang}
              onChange={(value) => setSpecific(key, value)}
            />
          )) : <p className="muted">{text.noCustom}</p>}
        </section>
        <button className="primary" onClick={onSave} disabled={saving}>
          {saving ? (uiLang === "zh" ? "保存中…" : "Saving…") : text.save}
        </button>
      </main>
    </div>
  );
}
