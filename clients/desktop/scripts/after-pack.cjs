'use strict';

const { writeFile } = require('node:fs/promises');
const path = require('node:path');

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'linux') return;
  // Electron Builder's generated AppImage launcher disables Chromium's sandbox
  // when user namespaces are unavailable. Supply the app's own launcher instead:
  // AppImage packaging copies it over the generated launcher from appOutDir.
  const launcher = '#!/bin/sh\nset -eu\nAPPDIR="${APPDIR:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)}"\nexec "$APPDIR/freecollab" "$@"\n';
  await writeFile(path.join(context.appOutDir, 'AppRun'), launcher, { mode: 0o755 });
};
