import { findConflicts } from './src/bookings.ts';

const conflicts = findConflicts([{ id: 'morning', start: 9, end: 10 }], { start: 10, end: 11 });
if (conflicts.length !== 1 || conflicts[0] !== 'morning') {
  throw new Error('The reported touching-booking defect is not reproduced');
}
console.log('Reproduced: the booking ending at 10 conflicts with one starting at 10');
