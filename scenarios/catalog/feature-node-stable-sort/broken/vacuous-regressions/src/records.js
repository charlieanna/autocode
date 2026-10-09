'use strict';

function byId(records) {
  return records.slice().sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
}

function sortByScore(records, direction = 'asc') {
  if (direction !== 'asc' && direction !== 'desc') {
    throw new RangeError('direction must be asc or desc');
  }
  if (!Array.isArray(records) || !Array.from(records).every((row) =>
    row !== null && typeof row === 'object' && typeof row.id === 'string' && Number.isFinite(row.score))) {
    throw new TypeError('records must have string ids and finite numeric scores');
  }
  const sign = direction === 'asc' ? 1 : -1;
  return records.map((row, index) => ({ row, index })).sort((left, right) =>
    sign * (left.row.score - right.row.score) || left.index - right.index).map(({ row }) => row);
}

module.exports = { byId, sortByScore };
