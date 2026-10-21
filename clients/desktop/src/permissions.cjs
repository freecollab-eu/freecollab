'use strict';
/** Per-installation, in-memory grants. A check never grants an unapproved device. */
class PermissionGrants {
  constructor() { this.granted = new Set(); }
  keys(permission, details = {}) {
    if (permission === 'media') {
      const types = details.mediaTypes || (details.mediaType ? [details.mediaType] : []);
      return [...new Set(types.filter(type => type === 'audio' || type === 'video'))].map(type => 'media:' + type);
    }
    return ['notifications', 'fullscreen', 'clipboard-sanitized-write'].includes(permission) ? [permission] : [];
  }
  check(permission, details) {
    const keys = this.keys(permission, details);
    return keys.length > 0 && keys.every(key => this.granted.has(key));
  }
  approve(permission, details) { for (const key of this.keys(permission, details)) this.granted.add(key); }
}
module.exports = { PermissionGrants };
