# Record ordering

The dependency-free CommonJS module exports `byId(records)`, which returns a
new array in ascending identifier order without changing the original array.
Equal identifiers preserve their input order.

Run native tests with `node --test tests/*.test.js`.
