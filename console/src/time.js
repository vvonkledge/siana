/**
 * Clock arithmetic, kept in one place because every screen here is an age.
 *
 * Nothing this console shows is worth anything without saying when it was true. A
 * queue read four minutes ago and a queue read four hours ago look identical on a
 * phone, and the second one is a fleet nobody is watching.
 */

/** How far ahead of this machine's clock a stamp has to be before it is called out.
 *
 * A stamp from the future is a real thing to see - a record written on another
 * machine, or a clock that stepped - and calling it "0s ago" would hide it. A second
 * of slack, because two clocks agreeing to the second is not something to report. */
const AHEAD_MS = -1000;

const MINUTE = 60_000;
const HOUR = 3_600_000;
const DAY = 86_400_000;

/** A store's timestamp as milliseconds, or null when it will not parse.
 *
 * Null rather than a guess. A record whose stamp was hand-edited is a record whose
 * age nobody knows, and rendering "0s ago" over it would be this console inventing
 * the one field it exists to report. */
export function parseStamp(text) {
  if (typeof text !== 'string' || !text.trim()) return null;
  const ms = Date.parse(text);
  return Number.isFinite(ms) ? ms : null;
}

/** An age in milliseconds, said the way a person reads one.
 *
 * Two units at most, and never a decimal: this is read at a glance, and the
 * difference that matters is between minutes and hours, never between 4.2 and 4.3
 * hours. */
export function sayAge(ms) {
  if (!Number.isFinite(ms)) return 'age unknown';
  // A stamp ahead of this machine's clock is a real thing to see - a record written
  // on another machine, or a clock that stepped - and calling it "0s ago" would
  // hide it.
  if (ms < -1000) return 'ahead of this clock';
  if (ms < MINUTE) return `${Math.max(0, Math.floor(ms / 1000))}s`;
  if (ms < HOUR) return `${Math.floor(ms / MINUTE)}m`;
  if (ms < DAY) {
    const hours = Math.floor(ms / HOUR);
    const minutes = Math.floor((ms % HOUR) / MINUTE);
    return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
  }
  const days = Math.floor(ms / DAY);
  const hours = Math.floor((ms % DAY) / HOUR);
  return hours ? `${days}d ${hours}h` : `${days}d`;
}

/** How old a stamp is, said, or the reason it cannot be said.
 *
 * `duration` is what a caller reads before it appends anything. Two of the answers
 * above are whole phrases rather than lengths of time, and "ahead of this clock ago"
 * is what happens to a caller that does not check. */
export function ageOf(text, now) {
  const at = parseStamp(text);
  if (at === null) {
    return { known: false, duration: false, ms: null, said: 'age unknown' };
  }
  const ms = now - at;
  return { known: true, duration: ms >= AHEAD_MS, ms, said: sayAge(ms) };
}
