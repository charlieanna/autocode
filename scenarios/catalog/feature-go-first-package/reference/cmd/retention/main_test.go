package main_test

import (
	"bytes"
	"context"
	"errors"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func TestCLIStreamsAndExits(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "retention")
	buildContext, cancelBuild := context.WithTimeout(context.Background(), time.Minute)
	defer cancelBuild()
	if output, err := exec.CommandContext(buildContext, "go", "build", "-o", binary, ".").CombinedOutput(); err != nil {
		t.Fatalf("build retention: %v\n%s", err, output)
	}
	cases := []struct {
		name   string
		args   []string
		stdout string
		stderr string
		exit   int
	}{
		{"no_arguments", nil, "30\n", "", 0},
		{"german", []string{"de"}, "7\n", "", 0},
		{"exception", []string{"exception"}, "1\n", "", 0},
		{"uppercase_is_default", []string{"DE"}, "30\n", "", 0},
		{"leading_space_is_default", []string{" de"}, "30\n", "", 0},
		{"domain_is_default", []string{"de.com"}, "30\n", "", 0},
		{"unknown_is_default", []string{"net"}, "30\n", "", 0},
		{"extra_arguments", []string{"de", "exception"}, "", "usage: retention [TLD]\n", 2},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			runContext, cancelRun := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancelRun()
			command := exec.CommandContext(runContext, binary, tc.args...)
			var stdout, stderr bytes.Buffer
			command.Stdout, command.Stderr = &stdout, &stderr
			exit := 0
			if err := command.Run(); err != nil {
				var failure *exec.ExitError
				if !errors.As(err, &failure) {
					t.Fatalf("run retention: %v", err)
				}
				exit = failure.ExitCode()
			}
			if exit != tc.exit || stdout.String() != tc.stdout || stderr.String() != tc.stderr {
				t.Fatalf("retention %q = (exit %d, stdout %q, stderr %q), want (%d, %q, %q)", tc.args, exit, stdout.String(), stderr.String(), tc.exit, tc.stdout, tc.stderr)
			}
		})
	}
}
