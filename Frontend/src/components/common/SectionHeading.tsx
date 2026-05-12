interface SectionHeadingProps {
  eyebrow: string
  title: string
  description: string
}

export function SectionHeading({
  eyebrow,
  title,
  description,
}: SectionHeadingProps) {
  return (
    <div className="mx-auto max-w-3xl space-y-4 text-center">
      <p className="text-sm font-semibold uppercase tracking-[0.35em] text-accent">
        {eyebrow}
      </p>
      <h2 className="text-3xl font-semibold tracking-tight text-white md:text-5xl">
        {title}
      </h2>
      <p className="text-base leading-7 text-text-secondary md:text-lg">
        {description}
      </p>
    </div>
  )
}
