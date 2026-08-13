import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '../../lib/cn'
import { Button, type ButtonProps } from './Button'
import { useToast } from './Toast'

/**
 * Copy `text` to the clipboard, confirming with a transient ✓ (2s) in place of the
 * copy icon — and, when `label` is given, swapping it for "Copied". Falls back to a
 * toast when the browser blocks the write. The single copy affordance shared by the
 * MCP panel, the reveal-once secret panel, and the permission id chips.
 */
export function CopyButton({
  text,
  label,
  variant = 'secondary',
  size = 'sm',
  iconClassName = 'h-4 w-4',
  ...props
}: Omit<ButtonProps, 'children'> & {
  text: string
  label?: string
  iconClassName?: string
}) {
  const [copied, setCopied] = useState(false)
  const { t } = useTranslation()
  const toast = useToast()
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error(t('ui.copyButton.failed'))
    }
  }
  return (
    <Button
      type="button"
      variant={variant}
      size={size}
      aria-label={label ?? t('common.copy')}
      {...props}
      onClick={copy}
    >
      {copied ? (
        <Check className={cn(iconClassName, 'text-ok')} />
      ) : (
        <Copy className={iconClassName} />
      )}
      {label != null && <span>{copied ? t('common.copied') : label}</span>}
    </Button>
  )
}
