import { useStore } from "../store";

export const THEMES: { id: string; name: string }[] = [
  { id: "midnight", name: "Midnight" },
  { id: "graphite", name: "Graphite" },
  { id: "nord", name: "Nord" },
  { id: "solarized", name: "Solarized" },
  { id: "rose", name: "Rosé" },
  { id: "emerald", name: "Emerald" },
  { id: "daylight", name: "Daylight" },
];

export function ThemePicker({ compact = false }: { compact?: boolean } = {}) {
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);

  return (
    <label className={`theme-picker${compact ? " compact" : ""}`} title="Color theme">
      🎨
      <select value={theme} onChange={(e) => setTheme(e.target.value)}>
        {THEMES.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </select>
    </label>
  );
}
