import { EventShieldColour, EventShieldReason, type EventEncryptionInfo } from 'matrix-js-sdk/lib/crypto-api';

/** An exact verified device key match is additionally required by the caller. */
export function acceptableWorkEncryptionOrigin(info: EventEncryptionInfo | null) {
  if (!info || ![EventShieldColour.NONE, EventShieldColour.GREY, EventShieldColour.RED].includes(info.shieldColour)) return false;
  if (info.shieldReason === null) return info.shieldColour === EventShieldColour.NONE;
  // Manual fingerprints are useful without cross-signing. All authenticity,
  // identity-change, unknown-device and sender-mismatch warnings fail closed.
  return [EventShieldReason.UNSIGNED_DEVICE, EventShieldReason.UNVERIFIED_IDENTITY].includes(info.shieldReason);
}
