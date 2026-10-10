import subprocess
import sys
import unittest


class HiddenTemperatureTests(unittest.TestCase):
    def invoke(self, args):
        return subprocess.run([sys.executable, "-m", "temperature", *args], capture_output=True, text=True, timeout=10)

    def test_conversion_grid(self):
        for unit, values in [
            ("C", [-273.15, -40, -12.5, 0, 37, 100, 1234.2]),
            ("F", [-459.67, -40, 0, 32, 98.6, 212, 500.25]),
        ]:
            for value in values:
                with self.subTest(unit=unit, value=value):
                    expected = value * 1.8 + 32 if unit == "C" else (value - 32) / 1.8
                    suffix = "F" if unit == "C" else "C"
                    p = self.invoke([str(value), unit])
                    self.assertEqual((p.returncode, p.stderr), (0, ""))
                    if unit == "F" and value == -459.67:
                        # Absolute zero converts to a decimal rounding tie. The
                        # brief specifies precision, not a tie-breaking rule.
                        self.assertIn(p.stdout, ("-273.1 C\n", "-273.2 C\n"))
                    else:
                        self.assertEqual(p.stdout, f"{expected:.1f} {suffix}\n")

    def test_rejects_nonfinite_below_zero_and_shape(self):
        for args in [
            ["nan", "C"],
            ["inf", "F"],
            ["-inf", "C"],
            ["-273.16", "C"],
            ["-459.68", "F"],
            ["0", "K"],
            ["0", "c"],
            [],
            ["1"],
            ["0", "C", "extra"],
        ]:
            with self.subTest(args=args):
                p = self.invoke(args)
                self.assertEqual((p.returncode, p.stdout), (2, ""))
                self.assertTrue(p.stderr)
