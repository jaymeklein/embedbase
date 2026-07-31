/** Minimal store-only (uncompressed) ZIP writer — enough to bundle a few UTF-8 text files into one
 *  downloadable archive without pulling in a dependency. The store method (0) is universally
 *  unzippable (Windows Explorer, macOS, `unzip`, Python `zipfile`); we never need compression for a
 *  handful of small markdown docs. Names and content are flagged UTF-8 (general-purpose bit 11). Not
 *  ZIP64: every entry and the archive must stay under 4 GiB — trivially true for a skill bundle. */

/** One file to place in the archive. `name` may contain forward slashes to nest it in a folder. */
export type ZipEntry = { name: string; content: string }

// CRC-32 (IEEE 802.3) lookup table — each ZIP entry records the checksum of its uncompressed bytes.
const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n += 1) {
    let c = n
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c >>> 0
  }
  return table
})()

function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff
  for (let i = 0; i < bytes.length; i += 1) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

const u16 = (n: number): Uint8Array => new Uint8Array([n & 0xff, (n >> 8) & 0xff])
const u32 = (n: number): Uint8Array =>
  new Uint8Array([n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, (n >>> 24) & 0xff])

function concat(parts: Uint8Array[]): Uint8Array<ArrayBuffer> {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0))
  let offset = 0
  for (const part of parts) {
    out.set(part, offset)
    offset += part.length
  }
  return out
}

/** Build a store-only ZIP from UTF-8 text entries and return it as an `application/zip` Blob. */
export function buildZip(entries: ZipEntry[]): Blob {
  const encoder = new TextEncoder()
  const locals: Uint8Array[] = [] // local headers + file data, in archive order
  const central: Uint8Array[] = [] // central-directory headers
  let offset = 0 // running byte offset of each local header, recorded in the central directory

  for (const entry of entries) {
    const name = encoder.encode(entry.name)
    const data = encoder.encode(entry.content)
    const crc = crc32(data)
    const size = data.length // store method → compressed size == uncompressed size

    const localHeader = concat([
      u32(0x04034b50), // local file header signature ("PK\x03\x04")
      u16(20), // version needed to extract (2.0)
      u16(0x0800), // general-purpose flags: bit 11 → names & content are UTF-8
      u16(0), // compression method: 0 = store
      u16(0), // last-mod time
      u16(0), // last-mod date
      u32(crc),
      u32(size),
      u32(size),
      u16(name.length),
      u16(0), // extra-field length
      name,
    ])
    locals.push(localHeader, data)

    central.push(
      concat([
        u32(0x02014b50), // central directory header signature ("PK\x01\x02")
        u16(20), // version made by
        u16(20), // version needed to extract
        u16(0x0800), // general-purpose flags: bit 11 → UTF-8
        u16(0), // method: store
        u16(0), // time
        u16(0), // date
        u32(crc),
        u32(size),
        u32(size),
        u16(name.length),
        u16(0), // extra length
        u16(0), // comment length
        u16(0), // disk number start
        u16(0), // internal attributes
        u32(0), // external attributes
        u32(offset), // relative offset of the local header
        name,
      ]),
    )
    offset += localHeader.length + data.length
  }

  const centralBytes = concat(central)
  const end = concat([
    u32(0x06054b50), // end-of-central-directory signature ("PK\x05\x06")
    u16(0), // number of this disk
    u16(0), // disk where the central directory starts
    u16(entries.length), // central-directory entries on this disk
    u16(entries.length), // total central-directory entries
    u32(centralBytes.length), // size of the central directory
    u32(offset), // offset of the start of the central directory
    u16(0), // comment length
  ])

  // Merge into one ArrayBuffer-backed array so the Blob part is Uint8Array<ArrayBuffer> (a plain
  // Uint8Array is generic over ArrayBufferLike under TS 5.7+ and won't satisfy BlobPart).
  return new Blob([concat([...locals, centralBytes, end])], { type: 'application/zip' })
}
