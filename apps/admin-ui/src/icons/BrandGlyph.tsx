import type { SVGProps } from "react";

export function BrandGlyph({ size = 20, ...rest }: SVGProps<SVGSVGElement> & { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      <path d="M7 3 C 15 7, 15 11, 7 12 C 15 14, 15 17, 7 21" />
      <path d="M17 3 C 9 7, 9 11, 17 12 C 9 14, 9 17, 17 21" />
      <line x1="9" y1="7" x2="15" y2="7" opacity="0.5" />
      <line x1="9" y1="12" x2="15" y2="12" opacity="0.5" />
      <line x1="9" y1="17" x2="15" y2="17" opacity="0.5" />
    </svg>
  );
}
