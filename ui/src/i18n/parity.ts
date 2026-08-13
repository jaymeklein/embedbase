/**
 * Compile-time completeness guard: the two catalogs must have identical key sets.
 * A key present in one locale but missing from the other fails `tsc` (leaf values
 * are widened to `string`, so differing translations are fine). This module is
 * never imported at runtime — it exists purely so the typecheck covers it.
 */
import en from './locales/en/translation.json'
import ptBR from './locales/pt-BR/translation.json'

/** Same nested shape as `T`, but every leaf string widened to `string`. */
type Stringify<T> = { [K in keyof T]: T[K] extends string ? string : Stringify<T[K]> }

// Both assignments together enforce exact key parity: `_ptCoversEn` fails if
// `pt-BR` is missing an `en` key; `_enCoversPt` fails for the reverse.
export const _ptCoversEn: Stringify<typeof en> = ptBR
export const _enCoversPt: Stringify<typeof ptBR> = en
