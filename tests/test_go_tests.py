"""Literal Go commands retain native argv and cannot invent collection proof."""
import json
from pathlib import Path
import shlex
import tempfile
import unittest

import autocode_go_tests as go_tests
import autocode_verify as verify
import autocode_verification_schedule as schedule


class GoInvocationTests(unittest.TestCase):
    def test_absolute_executable_environment_and_quoted_selector_are_preserved(self):
        words = ['env', 'GOWORK=off', 'GOCACHE=/a cache', '/opt/homebrew/bin/go',
                 'test', './http2', '-run', '^TestProbe/(cancel|reserve)[12]$',
                 '-count=1', '-timeout=30s']
        command = shlex.join(words)
        invocation = go_tests.parse(command)
        self.assertIsNotNone(invocation)
        self.assertEqual(tuple(words[:3]), invocation.prefix)
        self.assertEqual('/opt/homebrew/bin/go', invocation.executable)
        self.assertEqual(tuple(words[5:]), invocation.arguments)
        self.assertEqual([*words[:5], '-json', *words[5:]],
                         shlex.split(invocation.instrument()))
        framework = verify.command_framework(command)
        self.assertEqual('go', framework.name)
        self.assertTrue(verify.expects_results(framework, command))
        self.assertEqual(invocation.instrument(), verify._with_results(framework, command, 'unused.xml'))

    def test_plain_go_and_quoted_executable_can_be_recognized(self):
        for command in ('go test', 'go test ./...', "'/a directory/go' test .",
                        '/usr/bin/env GOWORK=off go test .', '/tools/go.exe test .'):
            with self.subTest(command=command):
                self.assertIsNotNone(go_tests.parse(command))

    def test_shell_programs_expansions_and_other_executables_have_no_collection_claim(self):
        commands = ('cd http2 && go test .', 'go test . | tee log', 'go test .; true',
                    'go test . > log', 'go test $(printf .)', 'go test `printf .`',
                    'go test "$PACKAGE"', 'go test -run "\\$literal" .', 'go test ./[ab]',
                    'go test . # comment', 'sh -c "go test ."',
                    'env -i go test .', 'GOWORK=off go test .', '/tools/not-go test .',
                    'go vet .', "go test -run 'unclosed", 'go test .\ntrue')
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(go_tests.parse(command))
                self.assertIsNone(verify.command_framework(command))
                framework = verify.Framework('go', 'go test ./...')
                self.assertEqual(command, verify._with_results(framework, command, 'unused.xml'))
                self.assertFalse(verify.expects_results(framework, command))

    def test_quoted_and_escaped_regex_literals_do_not_expand(self):
        for command in ("go test -run 'Test(A|B)[12]$' .",
                        'go test -run "Test(A|B)[12]" .',
                        r'go test -run Test\(A\|B\)\[12\]\$ .'):
            with self.subTest(command=command):
                invocation = go_tests.parse(command)
                self.assertIsNotNone(invocation)
                self.assertEqual(['go', 'test', '-json', *shlex.split(command)[2:]],
                                 shlex.split(invocation.instrument()))

    def test_existing_reporter_and_test_program_flags_are_not_rewritten(self):
        for command in ('/tools/go test -json .', '/tools/go test -json=true .',
                        '/tools/go test -json=1 .', '/tools/go test -json=false .',
                        '/tools/go test -json=invalid .'):
            with self.subTest(command=command):
                self.assertEqual(command, go_tests.parse(command).instrument())
        for command in ('go test . -run -json=false', 'go test . -args -json=false',
                        'go test . -ldflags -json', 'go test . -json=true'):
            with self.subTest(command=command):
                invocation = go_tests.parse(command)
                self.assertFalse(invocation.json_disabled)
                self.assertEqual(['go', 'test', '-json', *shlex.split(command)[2:]],
                                 shlex.split(invocation.instrument()))

    def test_disabled_or_malformed_reporter_cannot_qualify_json_shaped_stdout(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'stdout.log'
            output.write_text('\n'.join(json.dumps(event) for event in (
                {'Action':'run', 'Package':'pager', 'Test':'TestProbe'},
                {'Action':'pass', 'Package':'pager', 'Test':'TestProbe'})))
            for flag in ('-json=false', '-json=0', '-json=invalid'):
                with self.subTest(flag=flag):
                    command = '/tools/go test ' + flag + ' .'
                    framework = verify.command_framework(command)
                    receipt = {'command':command, 'output':str(output), 'exit_code':0,
                               'timed_out':False}
                    receipt['results'] = verify.per_test_results(framework, receipt, 'unused.xml')
                    self.assertIsNone(receipt['results'])
                    self.assertFalse(schedule.complete_results(receipt))
