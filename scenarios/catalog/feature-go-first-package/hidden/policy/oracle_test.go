package policy_test

import (
	"bytes"
	"os/exec"
	"path/filepath"
	"strconv"
	"testing"

	"example.test/scenario/policy"
)

var oracleCases = []struct {
	tld  string
	days int
}{
	{"de", 7}, {"exception", 1}, {"", 30}, {"com", 30}, {"org", 30},
	{"DE", 30}, {"De", 30}, {"EXCEPTION", 30}, {" de", 30}, {"de ", 30},
	{"de.com", 30}, {"example.de", 30}, {"exception.org", 30}, {"dé", 30},
	{"\x00de", 30}, {"de\n", 30},
}

func TestOracleExactCaseSensitivePolicy(t *testing.T) {
	for _, row := range oracleCases {
		if got := policy.RetentionDays(row.tld); got != row.days {
			t.Errorf("RetentionDays(%q) = %d, want %d", row.tld, got, row.days)
		}
	}
}

func TestOracleRetentionCLI(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "retention.bin")
	if output, err := exec.Command("go", "build", "-o", binary, "../cmd/retention").CombinedOutput(); err != nil {
		t.Fatalf("build CLI: %v: %s", err, output)
	}
	for _, row := range oracleCases {
		if row.tld == "\x00de" {
			continue // An OS argument cannot contain NUL; the public function is checked above.
		}
		command := exec.Command(binary, row.tld)
		var stderr bytes.Buffer
		command.Stderr = &stderr
		output, err := command.Output()
		want := strconv.Itoa(row.days) + "\n"
		if err != nil || string(output) != want || stderr.Len() != 0 {
			t.Errorf("CLI %q = %q, stderr=%q, err=%v; want %q", row.tld, output, stderr.String(), err, want)
		}
	}
	if output, err := exec.Command(binary).Output(); err != nil || string(output) != "30\n" {
		t.Errorf("argument-free CLI = %q, err=%v; want 30", output, err)
	}
	command := exec.Command(binary, "de", "extra")
	var stdout, stderr bytes.Buffer
	command.Stdout, command.Stderr = &stdout, &stderr
	command.Run()
	if command.ProcessState == nil || command.ProcessState.ExitCode() == 0 || stdout.Len() != 0 || stderr.Len() == 0 {
		t.Errorf("extra CLI arguments: state=%v stdout=%q stderr=%q", command.ProcessState, stdout.String(), stderr.String())
	}
}
