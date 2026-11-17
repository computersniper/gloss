import { useId } from "react";

// Logos use theme CSS vars so they adapt to every theme.
//  - Gloss  (classic reader): light illuminating a page (annotation + illumination).
//  - GlossX (agent workbench, `x`): the same lit page, with an X at its lower
//    right, centred under the lamp and its rays.
export function Logo({ size = 22, x = false }: { size?: number; x?: boolean }) {
  const id = useId();
  if (x) {
    const grad = `${id}-g`, glow = `${id}-l`, cut = `${id}-m`;
    const small = size < 28;
    return (
      <svg width={size} height={size} viewBox="0 0 96 96" fill="none" aria-hidden="true">
        <defs>
          <linearGradient id={grad} x1="59.4" y1="57" x2="85.4" y2="83" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="var(--accent2)" />
            <stop offset="1" stopColor="var(--accent)" />
          </linearGradient>
          <radialGradient id={glow} cx="69.6" cy="25.6" r="24" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="var(--accent2)" stopOpacity="0.45" />
            <stop offset="1" stopColor="var(--accent2)" stopOpacity="0" />
          </radialGradient>
          {/* the page gives way around the X so the two never touch */}
          <mask id={cut} maskUnits="userSpaceOnUse" x="0" y="0" width="96" height="96">
            <rect width="96" height="96" fill="#fff" />
            <circle cx="72.4" cy="70" r="20.5" fill="#000" />
          </mask>
        </defs>
        {/* the Gloss mark: a page lit by a lamp … */}
        <circle cx="69.6" cy="25.6" r="24" fill={`url(#${glow})`} />
        <g mask={`url(#${cut})`}>
          <rect x="18" y="24" width="46" height="60" rx="6.4"
            fill="var(--accent2)" fillOpacity="0.14" stroke="var(--accent)" strokeWidth={small ? 6 : 4.6} />
          <g stroke="var(--accent)" strokeWidth={small ? 5.6 : 4.4} strokeLinecap="round">
            <line x1="29.2" y1="44" x2="52.8" y2="44" />
            <line x1="29.2" y1="56" x2="52.8" y2="56" />
            <line x1="29.2" y1="68" x2="44" y2="68" />
          </g>
        </g>
        <circle cx="69.6" cy="25.6" r="10" fill="var(--accent2)" />
        <g stroke="var(--accent2)" strokeWidth={small ? 6 : 5.2} strokeLinecap="round">
          <line x1="69.6" y1="6.4" x2="69.6" y2="12" />
          <line x1="85.6" y1="10" x2="81.6" y2="14" />
          <line x1="89.6" y1="25.6" x2="84" y2="25.6" />
          <line x1="84.8" y1="40.8" x2="80.8" y2="37.2" />
          <line x1="54.4" y1="10.4" x2="58" y2="14" />
        </g>
        {/* … and the X of the agent edition at the page's lower right */}
        <g stroke={`url(#${grad})`} strokeWidth={small ? 8.4 : 7} strokeLinecap="round">
          <line x1="59.4" y1="57" x2="85.4" y2="83" />
          <line x1="85.4" y1="57" x2="59.4" y2="83" />
        </g>
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      {/* page */}
      <rect x="4.5" y="6" width="11.5" height="15" rx="1.6"
        fill="var(--accent2)" fillOpacity="0.14" stroke="var(--accent)" strokeWidth="1.4" />
      <g stroke="var(--accent)" strokeWidth="1.2" strokeLinecap="round">
        <line x1="7.3" y1="11" x2="13.2" y2="11" />
        <line x1="7.3" y1="14" x2="13.2" y2="14" />
        <line x1="7.3" y1="17" x2="11" y2="17" />
      </g>
      {/* light illuminating the page */}
      <circle cx="17.4" cy="6.4" r="2.5" fill="var(--accent2)" />
      <g stroke="var(--accent2)" strokeWidth="1.3" strokeLinecap="round">
        <line x1="17.4" y1="1.6" x2="17.4" y2="3" />
        <line x1="21.4" y1="2.5" x2="20.4" y2="3.5" />
        <line x1="22.4" y1="6.4" x2="21" y2="6.4" />
        <line x1="21.2" y1="10.2" x2="20.2" y2="9.3" />
        <line x1="13.6" y1="2.6" x2="14.5" y2="3.5" />
      </g>
    </svg>
  );
}
