import { type HTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  interactive?: boolean
}

/** Surface container with a hairline border; lifts on hover when interactive. */
export function Card({ className, interactive, ...props }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-card border border-border bg-surface',
        interactive && 'cursor-pointer transition-shadow duration-150 hover:shadow-hover',
        className,
      )}
      {...props}
    />
  )
}
