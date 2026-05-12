interface Props {
  left: number
  height: number
}

export function TodayLine({ left, height }: Props) {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute top-0 z-10"
      style={{ left: left - 0.5, height }}
    >
      <div className="h-full w-px bg-brand/60" />
      <div className="absolute left-1/2 top-0 h-2 w-2 -translate-x-1/2 -translate-y-1 rounded-full bg-brand shadow-sm" />
    </div>
  )
}
