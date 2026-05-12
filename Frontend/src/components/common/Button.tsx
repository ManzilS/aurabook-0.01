import type { ButtonHTMLAttributes, PropsWithChildren } from 'react'
import { cn } from '../../lib/cn'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: 'sm' | 'md' | 'lg'
  fullWidth?: boolean
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    'border-transparent bg-accent text-night shadow-glow hover:bg-white',
  secondary:
    'border-white/10 bg-white/5 text-white hover:border-accent/40 hover:bg-accent/10',
  ghost: 'border-transparent bg-transparent text-text-secondary hover:text-white',
  danger:
    'border-danger/40 bg-danger/10 text-white hover:border-danger hover:bg-danger/20',
}

const sizeClasses = {
  sm: 'h-9 px-3 text-sm',
  md: 'h-11 px-4 text-sm',
  lg: 'h-12 px-5 text-base',
}

export function Button({
  children,
  className,
  fullWidth,
  size = 'md',
  variant = 'primary',
  ...props
}: PropsWithChildren<ButtonProps>) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center rounded-full border font-medium transition duration-200',
        variantClasses[variant],
        sizeClasses[size],
        fullWidth && 'w-full',
        props.disabled && 'cursor-not-allowed opacity-60',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
}
