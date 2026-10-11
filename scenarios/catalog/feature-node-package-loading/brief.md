Extend the existing dependency-free CommonJS calculator with multiply(a, b)
and clamp(value, lower, upper). Preserve add(a, b) and the existing tests.
Multiply returns the arithmetic product, including negative and fractional
numbers. Clamp returns value within the inclusive lower/upper bounds, or the
nearest bound outside them; throw RangeError when lower exceeds upper.
Inputs are finite numbers.

Move the implementation into src/impl.js. Make src/calculator.js re-export it
using require('#math'), with package.json imports mapping #math to ./src/impl.js.
Expose the same implementation as mapped-calculator/impl through package.json
exports, and keep the package root mapped to ./src/calculator.js. Preserve the
package name, all existing scripts and the CommonJS API. The start/deploy
scripts are unrelated to the suite and do not need to run. Add no dependencies.

Add native node:test cases under tests/ named test_c1_multiply_signed_numbers
and test_c2_clamp_bounds_and_reject_reversed_bounds. Both must fail on the
original source and pass with the feature. Run npm test --silent.
