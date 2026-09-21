// Precompress in CI, where Node is available; production only serves static bytes.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { brotliCompressSync, constants } from 'node:zlib';

const [source, destination] = process.argv.slice(2);
if (!source || !destination) throw new Error('Usage: node prepare-play-transfer.mjs PLAY OUTPUT');
mkdirSync(destination, { recursive: true });
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const manifest = {};
for (const name of ['dunecity.data', 'dunecity.wasm', 'dunecity.js', 'p2p-direct.js', 'shell.js', 'shell.css']) {
  const bytes = readFileSync(join(source, name));
  const encoded = brotliCompressSync(bytes, { params: {
    [constants.BROTLI_PARAM_QUALITY]: 11,
    [constants.BROTLI_PARAM_SIZE_HINT]: bytes.length,
  } });
  writeFileSync(join(destination, name + '.br'), encoded);
  manifest[name] = { sourceSha256: digest(bytes), encodedSha256: digest(encoded) };
  console.log(`${name}: ${bytes.length} -> ${encoded.length} bytes`);
}
writeFileSync(join(destination, 'transfer.json'), JSON.stringify(manifest, null, 2) + '\n');
