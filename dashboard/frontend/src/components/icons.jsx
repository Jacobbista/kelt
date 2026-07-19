// Small inline SVG icon set (lucide-style: 24x24, no fill, currentColor stroke,
// 1.6 stroke width, round caps/joins). No icon-library dependency so the bundle
// and the build stay simple. Size via the `size` prop; color via CSS `color`.

function Svg({ size = 22, children, ...rest }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

// Positioning / Northbound: crosshair with a fixed point (locate-fixed).
export const IconLocate = (p) => (
  <Svg {...p}>
    <line x1="2" y1="12" x2="5" y2="12" />
    <line x1="19" y1="12" x2="22" y2="12" />
    <line x1="12" y1="2" x2="12" y2="5" />
    <line x1="12" y1="19" x2="12" y2="22" />
    <circle cx="12" cy="12" r="6" />
    <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
  </Svg>
);

// Network exposure / NEF: connected nodes.
export const IconNetwork = (p) => (
  <Svg {...p}>
    <rect x="9" y="2" width="6" height="6" rx="1" />
    <rect x="3" y="16" width="6" height="6" rx="1" />
    <rect x="15" y="16" width="6" height="6" rx="1" />
    <path d="M12 8v4" />
    <path d="M6 16v-2h12v2" />
  </Svg>
);

// Edge / MEC: compute unit (cpu).
export const IconCpu = (p) => (
  <Svg {...p}>
    <rect x="6" y="6" width="12" height="12" rx="2" />
    <path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2" />
    <rect x="9.5" y="9.5" width="5" height="5" rx="1" />
  </Svg>
);

// Custom workload: package with a plus.
export const IconBoxPlus = (p) => (
  <Svg {...p}>
    <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0" />
    <path d="M3.3 7 12 12l8.7-5M12 22V12" />
    <path d="M16 19h6M19 16v6" />
  </Svg>
);

// Services hub (sidebar / hero): grid of tiles.
export const IconGrid = (p) => (
  <Svg {...p}>
    <rect x="3" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="3" width="7" height="7" rx="1.5" />
    <rect x="3" y="14" width="7" height="7" rx="1.5" />
    <rect x="14" y="14" width="7" height="7" rx="1.5" />
  </Svg>
);

// Live / health pulse.
export const IconActivity = (p) => (
  <Svg {...p}>
    <path d="M3 12h4l2 6 4-14 2 8h6" />
  </Svg>
);

export const IconArrowRight = (p) => (
  <Svg {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Svg>
);

export const IconArrowLeft = (p) => (
  <Svg {...p}>
    <path d="M19 12H5M11 6l-6 6 6 6" />
  </Svg>
);

export const IconPlus = (p) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);

export const IconTrash = (p) => (
  <Svg {...p}>
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M6 6l1 14a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-14" />
  </Svg>
);

export const IconRefresh = (p) => (
  <Svg {...p}>
    <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v5h-5" />
  </Svg>
);

export const IconCopy = (p) => (
  <Svg {...p}>
    <rect x="9" y="9" width="12" height="12" rx="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </Svg>
);

export const IconRocket = (p) => (
  <Svg {...p}>
    <path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z" />
    <path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z" />
    <path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5" />
  </Svg>
);

// Settings hub: identity (shield with a keyhole), branding (paint palette),
// storage (stacked disks).
export const IconShield = (p) => (
  <Svg {...p}>
    <path d="M12 3l7 3v5c0 4.4-2.9 8.3-7 10-4.1-1.7-7-5.6-7-10V6z" />
    <circle cx="12" cy="11" r="1.6" />
    <line x1="12" y1="12.6" x2="12" y2="15" />
  </Svg>
);

export const IconPalette = (p) => (
  <Svg {...p}>
    <path d="M12 3a9 9 0 100 18 2 2 0 001.6-3.2 2 2 0 011.6-3.2H18a3 3 0 003-3c0-4.8-4-8.6-9-8.6z" />
    <circle cx="8" cy="10" r="1" />
    <circle cx="12" cy="7.5" r="1" />
    <circle cx="16" cy="10" r="1" />
  </Svg>
);

export const IconDisk = (p) => (
  <Svg {...p}>
    <ellipse cx="12" cy="6" rx="8" ry="3" />
    <path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6" />
    <path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
  </Svg>
);
