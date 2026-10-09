export interface Interval {
  readonly start: number;
  readonly end: number;
}

export interface Booking extends Interval {
  readonly id: string;
}

function requireInterval(interval: Interval): void {
  if (!Number.isFinite(interval.start) || !Number.isFinite(interval.end) || interval.end < interval.start) {
    throw new RangeError('Intervals require finite endpoints and end >= start');
  }
}

export function findConflicts(bookings: readonly Booking[], requested: Interval): string[] {
  requireInterval(requested);
  for (const booking of bookings) requireInterval(booking);
  if (requested.start === requested.end) return [];
  return bookings
    .filter((booking) => booking.start < booking.end && booking.start <= requested.end && requested.start < booking.end)
    .map((booking) => booking.id);
}
