/** Trade Research logo: three rising candlesticks on a blue-to-green tile (same as public/favicon.svg). */
export default function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="Trade Research logo">
      <defs>
        <linearGradient id="tr-logo-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#3b7cff" />
          <stop offset="1" stopColor="#22c58b" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill="url(#tr-logo-bg)" />
      <g fill="#fff">
        <rect x="7.6" y="15" width="1.6" height="11" rx=".8" opacity=".8" />
        <rect x="5.9" y="18" width="5" height="6" rx="1.3" opacity=".8" />
        <rect x="15.2" y="9.5" width="1.6" height="13" rx=".8" opacity=".9" />
        <rect x="13.5" y="12" width="5" height="8" rx="1.3" opacity=".9" />
        <rect x="22.8" y="4.5" width="1.6" height="13" rx=".8" />
        <rect x="21.1" y="6.5" width="5" height="8.5" rx="1.3" />
      </g>
    </svg>
  );
}
