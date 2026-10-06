"""Independent component-calculus checks for symbolic matrix derivatives.

Expected values come from ordinary scalar differentiation of explicit entries,
not from upstream regression test expressions or the candidate's matrix
derivative implementation. Candidate imports are read-only and source-local.
"""
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
workspace = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(workspace))
checks = []


def check(name, function):
    try:
        function()
    except Exception as error:
        checks.append({"name": name, "ok": False,
                       "detail": type(error).__name__ + ": " + str(error)})
    else:
        checks.append({"name": name, "ok": True})


import sympy as sp
assert Path(sp.__file__).resolve().is_relative_to(workspace)


def explicit(expression):
    return expression.as_explicit() if hasattr(expression, "as_explicit") else expression


def equal_entries(actual, expected):
    actual, expected = explicit(actual), explicit(expected)
    assert actual.shape == expected.shape, (actual.shape, expected.shape)
    for actual_entry, expected_entry in zip(actual, expected):
        substitutions = {symbol: symbol.as_explicit()
                         for symbol in actual_entry.atoms(sp.MatrixSymbol)}
        actual_entry = actual_entry.xreplace(substitutions).doit()
        assert sp.simplify(actual_entry - expected_entry) == 0, (actual_entry, expected_entry)


def component_gradient(scalar, variable):
    scalar = scalar.doit()
    return sp.Matrix(variable.rows, variable.cols,
                     lambda i, j: sp.diff(scalar, variable[i, j]))


def trace_linearity():
    for rows, columns in ((2, 2), (3, 3), (2, 3), (3, 2)):
        matrix = sp.MatrixSymbol("L", rows, columns)
        norm = sp.Trace(matrix * matrix.T)
        expression = 3 * norm + 7
        if rows == columns:
            expression += 2 * sp.Trace(matrix) ** 2 - 5 * sp.Trace(matrix)
        entries = matrix.as_explicit()
        scalar = 3 * (entries * entries.T).trace() + 7
        if rows == columns:
            scalar += 2 * entries.trace() ** 2 - 5 * entries.trace()
        equal_entries(sp.diff(expression, matrix), component_gradient(scalar, matrix))


def affine_quadratic():
    for size in (2, 3):
        vector = sp.MatrixSymbol("v", size, 1)
        coefficients = sp.Matrix(size, size, lambda i, j: 2 * i - 3 * j + 4)
        linear = sp.Matrix(size, 1, lambda i, j: i + 6)
        expression = vector.T * coefficients * vector + vector.T * linear + 9 * sp.Identity(1)
        scalar = expression.as_explicit()[0, 0]
        gradient = component_gradient(scalar, vector)
        actual_gradient = sp.diff(expression, vector)
        equal_entries(actual_gradient, gradient)
        expected_hessian = gradient.jacobian(list(vector.as_explicit()))
        equal_entries(sp.diff(actual_gradient, vector), expected_hessian)


def trace_chain_rule():
    for size in (2, 4):
        matrix = sp.MatrixSymbol("C", size, size)
        trace = sp.Trace(matrix)
        scalar_trace = matrix.as_explicit().trace()
        for expression, scalar in ((1 / trace, 1 / scalar_trace),
                                   (trace ** -2, scalar_trace ** -2)):
            equal_entries(sp.diff(expression, matrix), component_gradient(scalar, matrix))


def normalized_vector():
    for size in (2, 3):
        vector = sp.MatrixSymbol("n", size, 1)
        expression = vector / sp.Trace(vector.T * vector)
        entries = vector.as_explicit()
        expected = (entries / (entries.T * entries)[0, 0]).jacobian(list(entries))
        equal_entries(sp.diff(expression, vector), expected)


def matrix_elements():
    factor = sp.Symbol("a")
    for rows, columns in ((2, 3), (3, 2)):
        matrix = sp.MatrixSymbol("E", rows, columns)
        other = sp.MatrixSymbol("F", rows, columns)
        unrelated = sp.MatrixSymbol("U", rows, columns)
        for i, j in ((0, 0), (rows - 1, columns - 1)):
            basis = sp.zeros(rows, columns)
            basis[i, j] = 1
            equal_entries(sp.diff(matrix[i, j], matrix), basis)
            equal_entries(sp.diff(matrix, matrix[i, j]), basis)
            equal_entries(sp.diff(matrix + factor * other, other[i, j]), factor * basis)
            equal_entries(sp.diff(matrix + factor * other, unrelated[i, j]), sp.zeros(rows, columns))
        equal_entries(sp.diff(matrix + factor * other, factor), other.as_explicit())


def matrix_powers():
    parameter = sp.Symbol("t")
    bases = (sp.Matrix([[parameter, 1], [2, parameter ** 2]]),
             sp.Matrix([[0, parameter], [3, 1]]))
    for base in bases:
        for power in (0, 1, 2, 3, 4):
            expression = sp.MatPow(base, power)
            expected = (base ** power).applyfunc(lambda entry: sp.diff(entry, parameter))
            equal_entries(sp.diff(expression, parameter), expected)


def mixed_explicit_array():
    matrix = sp.MatrixSymbol("A", 2, 2)
    expression = matrix.as_explicit() * matrix
    output = expression.as_explicit()
    actual = explicit(sp.diff(expression, matrix))
    assert actual.shape == (2, 2, 2, 2), actual.shape
    for i in range(2):
        for j in range(2):
            for k in range(2):
                for l in range(2):
                    expected = sp.diff(output[k, l], matrix[i, j])
                    assert sp.simplify(actual[i, j, k, l] - expected) == 0


def preservation():
    matrix = sp.MatrixSymbol("P", 3, 3)
    unrelated = sp.MatrixSymbol("Q", 3, 3)
    parameter = sp.Symbol("z")
    expression = sp.Trace(matrix * matrix.T)
    equal_entries(sp.diff(expression, matrix), 2 * matrix.as_explicit())
    equal_entries(sp.diff(expression, unrelated), sp.zeros(3, 3))
    equal_entries(sp.diff(matrix, parameter), sp.zeros(3, 3))
    scalar = parameter ** 4 + 3 * sp.sin(parameter)
    assert sp.simplify(sp.diff(scalar, parameter) - (4 * parameter ** 3 + 3 * sp.cos(parameter))) == 0


check("trace_linearity", trace_linearity)
check("affine_quadratic", affine_quadratic)
check("trace_chain_rule", trace_chain_rule)
check("normalized_vector", normalized_vector)
check("matrix_elements", matrix_elements)
check("matrix_powers", matrix_powers)
check("mixed_explicit_array", mixed_explicit_array)
check("preservation", preservation)

print(json.dumps({"checks": checks}))
