'use strict';

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

// A private runtime umask is correct for profiles and keys, but would make a
// root-installed Linux package unreadable to its ordinary desktop users.
// Only this packaging process and its children use the public artifact umask.
if (process.platform !== 'win32') process.umask(0o022);

// Icon copying preserves the source mode. Stage only this public asset with
// distribution permissions, without changing source or private runtime files.
const resources = fs.mkdtempSync(path.join(os.tmpdir(), 'freecollab-build-icon-'));
try {
  const icon = path.join(resources, 'icon.png');
  fs.writeFileSync(icon, fs.readFileSync(path.join(__dirname, '../resources/icon.png')), { mode: 0o644 });
  const result = spawnSync(process.execPath, [
    require.resolve('electron-builder/cli.js'), '--publish', 'never',
    '--config.icon=' + icon, ...process.argv.slice(2),
  ], { stdio: 'inherit' });
  if (result.error) console.error(result.error.message);
  process.exitCode = result.status ?? 1;
} finally {
  fs.rmSync(resources, { recursive: true, force: true });
}
