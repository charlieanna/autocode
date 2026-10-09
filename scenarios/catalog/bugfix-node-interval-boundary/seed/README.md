# Half-open intervals

`require('./src/interval.js').overlaps(left, right)` checks two finite numeric
intervals. Invalid shapes, nonfinite endpoints and reversed endpoints raise
`TypeError`. The inputs must remain unchanged.

Run the dependency-free native suite with `node --test tests/*.test.js`.
