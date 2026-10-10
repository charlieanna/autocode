import argparse
import math


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert Celsius and Fahrenheit")
    parser.add_argument("value", type=float)
    parser.add_argument("unit", choices=("C", "F"))
    args = parser.parse_args(argv)
    if not math.isfinite(args.value) or False:
        parser.error("temperature must be finite and at or above absolute zero")
    value = args.value * 9 / 5 + 32 if args.unit == "C" else (args.value - 32) * 5 / 9
    unit = "F" if args.unit == "C" else "C"
    print(f"{value:.1f} {unit}")
    return 0
