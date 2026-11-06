/** Inline stroke icon set sized like dsh's 14px glyph family (currentColor). */

import type { CSSProperties } from 'react'

interface IconProps {
  size?: number
  className?: string
  style?: CSSProperties
}

function base(size: number | undefined, className: string | undefined, style: CSSProperties | undefined) {
  return {
    width: size ?? 14,
    height: size ?? 14,
    viewBox: '0 0 16 16',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.5,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    className,
    style,
    'aria-hidden': true,
  }
}

function base24(size: number | undefined, className: string | undefined, style: CSSProperties | undefined) {
  return {
    ...base(size, className, style),
    viewBox: '0 0 24 24',
    strokeWidth: 1.75,
  }
}

export function PlusIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="M8 3v10M3 8h10" />
    </svg>
  )
}

export function SendArrowUpIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)} strokeWidth={1.6}>
      <path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" />
    </svg>
  )
}

export function StopIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)} fill="currentColor" stroke="none">
      <rect x="3.5" y="3.5" width="9" height="9" rx="2.5" />
    </svg>
  )
}

export function PanelRightIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <rect x="2" y="2.5" width="12" height="11" rx="2" />
      <path d="M9.5 2.5v11" />
    </svg>
  )
}

export function PencilIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="m3 13 .7-2.8L10.9 3a1.4 1.4 0 0 1 2 0l.1.1a1.4 1.4 0 0 1 0 2L5.8 12.3Z" />
    </svg>
  )
}

export function MoreIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)} fill="currentColor" stroke="none">
      <circle cx="3.5" cy="8" r="1.1" />
      <circle cx="8" cy="8" r="1.1" />
      <circle cx="12.5" cy="8" r="1.1" />
    </svg>
  )
}

export function PanelLeftIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <rect x="2" y="2.5" width="12" height="11" rx="2" />
      <path d="M6.5 2.5v11" />
    </svg>
  )
}

export function ChatIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="M13.5 8a5.5 5.5 0 0 1-8.1 4.84L2.5 13.5l.7-2.8A5.5 5.5 0 1 1 13.5 8Z" />
    </svg>
  )
}

export function PinIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="M9.5 2.5h4L13 6l-2 1.5L9.5 13.5 8 8.5 4.5 7 3 3.5h4L8 2.5Z" fill="currentColor" stroke="none" opacity="0.9" />
    </svg>
  )
}

export function TrashIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.6-8.5" />
    </svg>
  )
}

export function ForkIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <circle cx="4" cy="4" r="1.8" />
      <circle cx="4" cy="12" r="1.8" />
      <circle cx="12" cy="6" r="1.8" />
      <path d="M4 5.8v4.4M5.6 4.9 10.3 6" />
    </svg>
  )
}

export function SearchIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <circle cx="7" cy="7" r="4.2" />
      <path d="m10.4 10.4 3 3" />
    </svg>
  )
}

export function ChevronDownIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="m4 6.5 4 3.5 4-3.5" />
    </svg>
  )
}

export function ChevronRightIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="m6.5 4 3.5 4-3.5 4" />
    </svg>
  )
}

export function SunIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <circle cx="8" cy="8" r="3" />
      <path d="M8 1.5V3M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M12.6 3.4l-1 1M4.4 11.6l-1 1" />
    </svg>
  )
}

export function MoonIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="M13.5 9.5A5.6 5.6 0 0 1 6.5 2.6a5.7 5.7 0 1 0 7 6.9Z" />
    </svg>
  )
}

export function MonitorIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <rect x="2" y="3" width="12" height="8" rx="1.5" />
      <path d="M6 14h4M8 11v3" />
    </svg>
  )
}

export function FolderIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.7h4.5A1.5 1.5 0 0 1 14 6.2v5.3a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5Z" />
    </svg>
  )
}

export function BranchIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <circle cx="4.5" cy="3.5" r="1.5" />
      <circle cx="4.5" cy="12.5" r="1.5" />
      <circle cx="11.5" cy="5.5" r="1.5" />
      <path d="M4.5 5v6M6 5.5h2.5A3 3 0 0 1 11.5 8v1.5" />
    </svg>
  )
}

export function WarningIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="M8 2.5 14 13H2Z" />
      <path d="M8 6.5v3M8 11.2v.6" />
    </svg>
  )
}

export function InfoIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <circle cx="8" cy="8" r="5.8" />
      <path d="M8 7.2v3.6M8 5.1v.4" />
    </svg>
  )
}

export function CheckIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="m3.5 8.5 3 3 6-7" />
    </svg>
  )
}

export function SparkIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)} fill="currentColor" stroke="none">
      <path d="M8 1.5 9.6 6 14 8l-4.4 2L8 14.5 6.4 10 2 8l4.4-2Z" />
    </svg>
  )
}

export function FileIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="M9 2H4.5A1.5 1.5 0 0 0 3 3.5v9A1.5 1.5 0 0 0 4.5 14h7a1.5 1.5 0 0 0 1.5-1.5V6Z" />
      <path d="M9 2v4h4" />
    </svg>
  )
}

export function TerminalIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <rect x="2" y="2.5" width="12" height="11" rx="2" />
      <path d="m4.5 6 2 2-2 2M8 10.5h3.5" />
    </svg>
  )
}

export function ListTodoIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="m2.5 4.5 1.5 1.5 2.5-3M2.5 11l1.5 1.5L6.5 9.5M8.5 5h5M8.5 11.5h5" />
    </svg>
  )
}

export function BotIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <rect x="3" y="4.5" width="10" height="8" rx="2" />
      <path d="M8 4.5V2.5M6.5 8v1.5M9.5 8v1.5" />
    </svg>
  )
}

export function ActivityIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="M2 8.5h2.5L6 4l2.5 8L10.5 8H14" />
    </svg>
  )
}

export function XIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="m4 4 8 8M12 4l-8 8" />
    </svg>
  )
}

export function ArrowDownIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="M8 3v10M3.5 8.5 8 13l4.5-4.5" />
    </svg>
  )
}

export function CopyIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
      <path d="M3.5 10.5A1.5 1.5 0 0 1 2.5 9V3.5A1.5 1.5 0 0 1 4 2h5.5a1.5 1.5 0 0 1 1.5 1.5" />
    </svg>
  )
}

export function RefreshIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="M13.2 6.2A5.4 5.4 0 1 0 13.5 9" />
      <path d="M13.5 2.5v3.7H9.8" />
    </svg>
  )
}

export function ArrowLeftIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="M13 8H3M7.5 3.5 3 8l4.5 4.5" />
    </svg>
  )
}

/** lucide's Settings cog: a notched gear that cannot be read as the theme sun. */
export function SettingsIcon(props: IconProps) {
  return (
    <svg {...base24(props.size, props.className, props.style)}>
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.38a2 2 0 0 0-.73-2.73l-.15-.1a2 2 0 0 1-1-1.72v-.51a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  )
}

export function PaletteIcon(props: IconProps) {
  return (
    <svg {...base24(props.size, props.className, props.style)}>
      <circle cx="13.5" cy="6.5" r=".5" fill="currentColor" />
      <circle cx="17.5" cy="10.5" r=".5" fill="currentColor" />
      <circle cx="8.5" cy="7.5" r=".5" fill="currentColor" />
      <circle cx="6.5" cy="12.5" r=".5" fill="currentColor" />
      <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z" />
    </svg>
  )
}

export function BellIcon(props: IconProps) {
  return (
    <svg {...base(props.size, props.className, props.style)}>
      <path d="M4 11.5h8l-1-1.6V7a3 3 0 0 0-6 0v2.9l-1 1.6Z" />
      <path d="M6.6 13a1.5 1.5 0 0 0 2.8 0" />
    </svg>
  )
}
