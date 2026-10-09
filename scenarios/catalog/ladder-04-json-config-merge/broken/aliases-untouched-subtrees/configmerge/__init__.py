import argparse
import copy
import json
import sys


def merge(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        raise ValueError("both inputs must be JSON objects")
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recursively merge JSON configuration files")
    parser.add_argument("base")
    parser.add_argument("override")
    args = parser.parse_args(argv)
    try:
        with open(args.base, encoding="utf-8") as source:
            base = json.load(source)
        with open(args.override, encoding="utf-8") as source:
            override = json.load(source)
        result = merge(base, override)
    except (OSError, UnicodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0
