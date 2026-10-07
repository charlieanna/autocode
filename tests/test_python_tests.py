import shlex
import unittest

from autocode_python_tests import parse
from autocode_verification_schedule import collection_kind


class PythonTestCommandTests(unittest.TestCase):
    def test_targeted_pytest_keeps_interpreter_environment_and_collection_options(self):
        command = ("env PYTHONPATH='/support with spaces' PYTEST_PLUGINS=bootstrap "
                   "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 '/venv with spaces/bin/python' -m pytest "
                   "-q -o 'python_files=test*.py' -o minversion= -p no:cacheprovider tests/old")
        invocation = parse(command)
        self.assertEqual('pytest', collection_kind(command))
        self.assertEqual('/venv with spaces/bin/python', invocation.python)
        expected = shlex.split(command)[:-1] + ['tests/new.py']
        self.assertEqual(expected, shlex.split(invocation.targeted(['tests/new.py'])))

    def test_unsupported_option_runs_unchanged_suite_instead_of_guessing_its_argument(self):
        command = 'env MODE=proof python3 -m pytest --custom-option value tests/'
        self.assertEqual(shlex.split(command), shlex.split(parse(command).targeted(['test_new.py'])))

    def test_env_unittest_keeps_literal_environment_and_verbose_results(self):
        invocation = parse('env CONFIG=proof python3 -m unittest discover -v')
        self.assertEqual('env CONFIG=proof python3 -m unittest -v test_new.py',
                         invocation.targeted(['test_new.py']))

    def test_shell_programs_and_env_options_are_not_exact_collectors(self):
        for command in ('env -i python3 -m pytest', 'env -S "python3 -m pytest"',
                        'env X=$(echo proof) python3 -m pytest', 'python3 -m pytest && true',
                        'env X=1 python3 -m pytest; true', 'env X=1 bash -c "python3 -m pytest"',
                        'env X=1 python3 -m unittest', 'env X=1 python3 -m pytest\ntrue'):
            with self.subTest(command=command):
                self.assertIsNone(parse(command))
                self.assertIsNone(collection_kind(command))
