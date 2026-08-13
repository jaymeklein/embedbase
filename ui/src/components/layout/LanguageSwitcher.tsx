import { Check, Languages } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DropdownItem, DropdownMenu } from '../ui/DropdownMenu'
import { setLanguage } from '../../i18n'
import { SUPPORTED, type Lang } from '../../i18n/detect'

/** Endonyms — a language is always named in its own tongue, so never translated. */
const LANG_LABELS: Record<Lang, string> = {
  en: 'English',
  'pt-BR': 'Português (BR)',
}

/**
 * Topbar control to switch the console language between the supported locales.
 * Persistence + `<html lang>` / date-locale syncing are handled by the i18n layer
 * (`i18n/index.ts`), so this only has to call `changeLanguage`.
 */
export function LanguageSwitcher() {
  const { i18n, t } = useTranslation()
  const active = i18n.resolvedLanguage
  return (
    <DropdownMenu
      triggerAriaLabel={t('topbar.language')}
      triggerIcon={<Languages className="h-4 w-4" />}
    >
      {SUPPORTED.map((lang) => (
        <DropdownItem
          key={lang}
          icon={active === lang ? <Check className="h-4 w-4" /> : undefined}
          onSelect={() => setLanguage(lang)}
        >
          {LANG_LABELS[lang]}
        </DropdownItem>
      ))}
    </DropdownMenu>
  )
}
