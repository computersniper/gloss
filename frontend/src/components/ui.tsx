// GlossX UI primitives: popover menu, segmented control, switch, select, spinner.
// Styling lives in glossx.css (`gx-*` classes) and only uses design tokens.
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, LoaderCircle } from "lucide-react";

type Placement = "bottom-start" | "bottom-end" | "top-start" | "top-end";

/** Floating layer anchored to an element; closes on outside press, Escape, resize. */
export function Popover({
  anchor, open, onClose, placement = "bottom-start", width, className = "", children,
}: {
  anchor: HTMLElement | null;
  open: boolean;
  onClose: () => void;
  placement?: Placement;
  width?: number | "anchor";
  className?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [style, setStyle] = useState<CSSProperties>({ visibility: "hidden" });

  useLayoutEffect(() => {
    if (!open || !anchor || !ref.current) return;
    const a = anchor.getBoundingClientRect();
    const el = ref.current;
    const w = width === "anchor" ? a.width : width || el.offsetWidth;
    const h = el.offsetHeight;
    const gap = 6;
    let top = placement.startsWith("bottom") ? a.bottom + gap : a.top - gap - h;
    if (placement.startsWith("bottom") && top + h > window.innerHeight - 8 && a.top - gap - h > 8) top = a.top - gap - h;
    if (placement.startsWith("top") && top < 8) top = Math.min(a.bottom + gap, window.innerHeight - h - 8);
    let left = placement.endsWith("start") ? a.left : a.right - w;
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
    const next = { top: Math.round(Math.max(8, top)), left: Math.round(left), width: width ? Math.round(w) : undefined };
    setStyle((prev) =>
      prev.top === next.top && prev.left === next.left && prev.width === next.width && prev.visibility === "visible"
        ? prev
        : { ...next, visibility: "visible" });
  }); // re-measure after every render: content (and so height) changes while open

  useEffect(() => {
    if (!open) setStyle({ visibility: "hidden" });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (ref.current?.contains(t) || anchor?.contains(t)) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("mousedown", onDown, true);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", onClose);
    return () => {
      window.removeEventListener("mousedown", onDown, true);
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", onClose);
    };
  }, [open, anchor, onClose]);

  if (!open) return null;
  return createPortal(
    <div ref={ref} className={`gx-pop ${className}`} style={style} role="menu">
      {children}
    </div>,
    document.body,
  );
}

export function MenuItem({
  icon, label, detail, hint, selected, danger, disabled, onSelect,
}: {
  icon?: ReactNode;
  label: ReactNode;
  detail?: ReactNode;
  hint?: ReactNode;
  selected?: boolean;
  danger?: boolean;
  disabled?: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className={`gx-menu-item${selected ? " selected" : ""}${danger ? " danger" : ""}`}
      disabled={disabled}
      onClick={onSelect}
    >
      {icon !== undefined && <span className="gx-menu-icon">{icon}</span>}
      <span className="gx-menu-text">
        <span className="gx-menu-label">{label}</span>
        {detail && <span className="gx-menu-detail">{detail}</span>}
      </span>
      {hint && <span className="gx-menu-hint">{hint}</span>}
      {selected && <Check size={14} className="gx-menu-check" />}
    </button>
  );
}

export const MenuLabel = ({ children }: { children: ReactNode }) => <div className="gx-menu-heading">{children}</div>;
export const MenuSep = () => <div className="gx-menu-sep" />;

/** Button that opens a menu; `children` receives a close fn. */
export function MenuButton({
  label, title, className = "gx-btn ghost", placement, width, chevron = true, disabled, children,
}: {
  label: ReactNode;
  title?: string;
  className?: string;
  placement?: Placement;
  width?: number | "anchor";
  chevron?: boolean;
  disabled?: boolean;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const close = () => setOpen(false);
  return (
    <>
      <button
        ref={setAnchor}
        type="button"
        className={`${className}${open ? " open" : ""}`}
        title={title}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
      >
        {label}
        {chevron && <ChevronDown size={12} className="gx-chevron" />}
      </button>
      <Popover anchor={anchor} open={open} onClose={close} placement={placement} width={width}>
        {open && children(close)}
      </Popover>
    </>
  );
}

/** Custom select: a quiet field that opens a menu (replaces native <select>). */
export function Select<T extends string>({
  value, options, onChange, title, className = "", placement = "bottom-start", width = "anchor",
}: {
  value: T;
  options: { value: T; label: ReactNode; detail?: ReactNode; icon?: ReactNode }[];
  onChange: (v: T) => void;
  title?: string;
  className?: string;
  placement?: Placement;
  width?: number | "anchor";
}) {
  const current = options.find((o) => o.value === value);
  return (
    <MenuButton
      className={`gx-select ${className}`}
      title={title}
      placement={placement}
      width={width}
      label={<span className="gx-select-value">{current?.icon}{current?.label ?? value}</span>}
    >
      {(close) =>
        options.map((o) => (
          <MenuItem
            key={o.value}
            icon={o.icon}
            label={o.label}
            detail={o.detail}
            selected={o.value === value}
            onSelect={() => {
              close();
              onChange(o.value);
            }}
          />
        ))}
    </MenuButton>
  );
}

export function Segmented<T extends string>({
  value, options, onChange, size = "md", label,
}: {
  value: T;
  options: { value: T; label: ReactNode; icon?: ReactNode; title?: string }[];
  onChange: (v: T) => void;
  size?: "sm" | "md";
  label?: string;
}) {
  return (
    <div className={`gx-seg ${size}`} role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          className={o.value === value ? "on" : ""}
          title={o.title}
          onClick={() => onChange(o.value)}
        >
          {o.icon}
          <span>{o.label}</span>
        </button>
      ))}
    </div>
  );
}

export function Switch({ checked, onChange, label, hint, disabled }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode; hint?: ReactNode; disabled?: boolean }) {
  const control = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      className={`gx-switch${checked ? " on" : ""}`}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <i />
    </button>
  );
  if (!label) return control;
  return (
    <label className="gx-switch-row">
      <span>
        <span className="gx-switch-label">{label}</span>
        {hint && <span className="gx-switch-hint">{hint}</span>}
      </span>
      {control}
    </label>
  );
}

export const Spinner = ({ size = 14 }: { size?: number }) => <LoaderCircle size={size} className="gx-spin" aria-hidden="true" />;

export function EmptyState({ icon, title, hint, action }: { icon?: ReactNode; title: ReactNode; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="gx-empty">
      {icon && <div className="gx-empty-icon">{icon}</div>}
      <div className="gx-empty-title">{title}</div>
      {hint && <div className="gx-empty-hint">{hint}</div>}
      {action}
    </div>
  );
}
