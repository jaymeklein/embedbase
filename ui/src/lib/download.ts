/** Save a Blob to the user's downloads as `filename`, via a synthetic `<a download>` click.
 *
 * The object URL is revoked on a delay, not synchronously: `a.click()` only *queues* the browser's
 * async read of the blob, so revoking on the same tick can race that read and abort the download.
 * 60s is the same grace the document-download helpers in `api/client.ts` use. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}
