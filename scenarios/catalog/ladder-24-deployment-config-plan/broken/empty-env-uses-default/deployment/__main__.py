import argparse
import json
import sys
from pathlib import Path

from . import write_plan


def main():
    parser = argparse.ArgumentParser(description="Validate and write a local deployment plan")
    parser.add_argument("manifest")
    parser.add_argument("--env", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        write_plan(json.loads(Path(args.manifest).read_text()), json.loads(Path(args.env).read_text()), args.output)
    except ValueError as error:
        print("error: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
