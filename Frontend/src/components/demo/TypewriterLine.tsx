interface TypewriterLineProps {
  text: string
}

export function TypewriterLine({ text }: TypewriterLineProps) {
  return <span className="typewriter block max-w-full break-words">{text}</span>
}
