'use strict';

function byId(records) {
  return records.slice().sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
}

module.exports = { byId };
