/** Native password creation/reset rules; keep passwords exactly as entered. */
export function newPasswordValidationError(password: string): string | null {
  if ([...password].length < 12) return 'New passwords must contain at least 12 characters.';
  if (new TextEncoder().encode(password).length > 72) return 'This password is too long. Try a shorter one.';
  if (password.includes('\0')) return 'Passwords cannot contain a null character.';
  // TextEncoder replaces lone surrogates, while the API requires valid UTF-8.
  for (const character of password) {
    const point = character.codePointAt(0)!;
    if (point >= 0xd800 && point <= 0xdfff) return 'Use valid Unicode characters in your password.';
  }
  return null;
}
