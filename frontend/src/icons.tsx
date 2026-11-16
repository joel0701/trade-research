/** Small inline icons (stroke-based, inherit currentColor). */
const base = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
               strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

export const Paperclip = () => (
  <svg {...base}><path d="m21.4 11.1-8.5 8.5a6 6 0 0 1-8.5-8.5l8.5-8.5a4 4 0 0 1 5.7 5.7l-8.5 8.5a2 2 0 0 1-2.8-2.8l7.8-7.8" /></svg>
);
export const Sparkles = () => (
  <svg {...base}><path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z" /><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z" /></svg>
);
export const Chevron = () => <svg {...base} width={14} height={14}><path d="m6 9 6 6 6-6" /></svg>;
export const ArrowUp = () => <svg {...base} width={18} height={18} strokeWidth={2.2}><path d="M12 19V5M5 12l7-7 7 7" /></svg>;
export const Close = () => <svg {...base} width={12} height={12} strokeWidth={2.2}><path d="M18 6 6 18M6 6l12 12" /></svg>;
export const FileIcon = () => (
  <svg {...base} width={14} height={14}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg>
);
export const Check = () => <svg {...base} width={14} height={14} strokeWidth={2.4}><path d="M20 6 9 17l-5-5" /></svg>;
export const Sun = () => (
  <svg {...base}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
);
export const Moon = () => <svg {...base}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>;
/** Model badge: a small starburst tinted with the vendor's color. */
export const ModelMark = ({ color = "#d97757" }: { color?: string }) => (
  <svg width={16} height={16} viewBox="0 0 24 24" aria-hidden="true">
    <rect x="1" y="1" width="22" height="22" rx="5" fill={color} />
    <path d="M12 5v14M5 12h14M7 7l10 10M17 7 7 17" stroke="#fff" strokeWidth="2" strokeLinecap="round" />
  </svg>
);
