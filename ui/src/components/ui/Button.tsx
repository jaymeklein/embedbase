import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'
import { Spinner } from './Spinner'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
}

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent text-white hover:bg-accent-hover',
  secondary: 'bg-surface text-ink border border-border hover:bg-canvas',
  ghost: 'bg-transparent text-ink-muted hover:bg-canvas hover:text-ink',
  danger: 'bg-err text-white hover:brightness-95',
}

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-[13px] gap-1.5',
  md: 'h-9 px-4 text-sm gap-2',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', loading = false, className, children, disabled, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center justify-center rounded-control font-medium',
        'transition-colors duration-150 disabled:pointer-events-none disabled:opacity-50',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    >
      {loading && <Spinner className="h-3.5 w-3.5" />}
      {children}
    </button>
  )
})
