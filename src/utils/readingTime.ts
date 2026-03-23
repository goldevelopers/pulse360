/** Estimate reading time in minutes from raw markdown body text. */
export function getReadingTime(text: string): number {
  const words = text.trim().split(/\s+/).length;
  return Math.max(1, Math.round(words / 230));
}
