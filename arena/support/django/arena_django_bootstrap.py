"""Configure upstream Django test apps for pytest, using candidate-local source."""
from pathlib import Path
import sys

database_state = None


def pytest_configure(config):
    root = Path.cwd().resolve()
    if not (root / "django" / "__init__.py").is_file():
        raise RuntimeError("Django Arena test command must run from candidate root")
    sys.path.insert(0, str(root / "tests"))
    sys.path.insert(0, str(root))
    import django
    if not Path(django.__file__).resolve().is_relative_to(root):
        raise RuntimeError("Django tests imported a different source tree")
    from django.conf import settings
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.sites", "filtered_relation", "queries"],
        DATABASES={
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
            "other": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
        },
        DEFAULT_AUTO_FIELD="django.db.models.AutoField",
        SECRET_KEY="arena-local-test-setup",
        USE_TZ=False,
        PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    )
    django.setup()


def pytest_sessionstart(session):
    global database_state
    from django.test.utils import setup_databases, setup_test_environment
    setup_test_environment()
    database_state = setup_databases(verbosity=0, interactive=False, serialized_aliases=set())


def pytest_sessionfinish(session, exitstatus):
    from django.test.utils import teardown_databases, teardown_test_environment
    if database_state is not None:
        teardown_databases(database_state, verbosity=0)
    teardown_test_environment()
