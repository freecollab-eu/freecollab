#!/usr/bin/env node
'use strict';
// Offline publisher command. The private signing key is never part of the app.
const fs = require('node:fs/promises');
const crypto = require('node:crypto');
async function main() {
  const [manifestFile, signingKeyFile, outputFile] = process.argv.slice(2);
  if (!manifestFile || !signingKeyFile || !outputFile) throw new Error('Usage: node sign-update.cjs manifest.json private-ed25519.pem signed-update.json');
  const manifest = JSON.parse(await fs.readFile(manifestFile, 'utf8'));
  const key = crypto.createPrivateKey(await fs.readFile(signingKeyFile));
  if (key.asymmetricKeyType !== 'ed25519') throw new Error('Use an Ed25519 publisher signing key.');
  const payload = Buffer.from(JSON.stringify(manifest));
  await fs.writeFile(outputFile, JSON.stringify({ payload: payload.toString('base64'), signature: crypto.sign(null, payload, key).toString('base64') }) + '\n');
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
