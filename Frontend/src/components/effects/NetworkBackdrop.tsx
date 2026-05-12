export function NetworkBackdrop() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="grid-mask absolute inset-0 animate-pulsegrid opacity-60" />
      <div className="absolute -left-24 top-20 h-72 w-72 rounded-full bg-accent/20 blur-3xl animate-float" />
      <div className="absolute right-0 top-24 h-64 w-64 rounded-full bg-success/15 blur-3xl animate-float-slow" />
      <div className="absolute bottom-12 right-1/4 h-56 w-56 rounded-full bg-warning/10 blur-3xl animate-float" />
      <svg
        className="absolute inset-0 h-full w-full opacity-20"
        viewBox="0 0 800 800"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M83 145L203 245L354 214L458 320L622 255L719 366"
          stroke="rgba(0,212,255,0.55)"
          strokeWidth="2"
        />
        <path
          d="M146 542L286 448L402 511L518 424L661 505"
          stroke="rgba(0,255,136,0.45)"
          strokeWidth="2"
        />
        {[
          [83, 145],
          [203, 245],
          [354, 214],
          [458, 320],
          [622, 255],
          [719, 366],
          [146, 542],
          [286, 448],
          [402, 511],
          [518, 424],
          [661, 505],
        ].map(([cx, cy]) => (
          <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="5" fill="rgba(255,255,255,0.8)" />
        ))}
      </svg>
    </div>
  )
}
