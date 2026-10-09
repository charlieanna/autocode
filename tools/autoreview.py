"""Run only the autoreview unit, using the shared durable runtime."""
try:
    from . import autocode as runtime
except ImportError:
    import autocode as runtime


def cli():
    return runtime.cli(unit="autoreview")


if __name__ == "__main__":
    raise SystemExit(cli())
