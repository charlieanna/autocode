package window_test

import (
	"math"
	"os/exec"
	"path/filepath"
	"testing"

	"example.test/scenario/window"
)

func TestOracleHalfOpenVectors(t *testing.T) {
	intervals := [][2]int64{
		{10, 20}, {-10, -2}, {-1, 1}, {0, 0}, {7, 7}, {20, 10},
		{math.MinInt64, math.MaxInt64}, {math.MinInt64, math.MinInt64 + 1},
		{math.MaxInt64 - 1, math.MaxInt64},
	}
	values := []int64{math.MinInt64, math.MinInt64 + 1, -11, -10, -3, -2,
		-1, 0, 1, 7, 9, 10, 15, 19, 20, 21, math.MaxInt64 - 1, math.MaxInt64}
	for _, interval := range intervals {
		for _, value := range values {
			want := interval[0] <= value && value < interval[1]
			if got := window.Contains(interval[0], interval[1], value); got != want {
				t.Errorf("Contains(%d, %d, %d) = %t, want %t", interval[0], interval[1], value, got, want)
			}
		}
	}
}

func TestOracleMembershipCLI(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "contains.bin")
	build := exec.Command("go", "build", "-o", binary, "../cmd/contains")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build CLI: %v: %s", err, output)
	}
	for _, row := range []struct {
		args   []string
		output string
		exit   int
	}{
		{[]string{"10", "20", "10"}, "true\n", 0},
		{[]string{"10", "20", "20"}, "false\n", 1},
		{[]string{"7", "7", "7"}, "false\n", 1},
		{[]string{"-10", "-2", "-10"}, "true\n", 0},
	} {
		command := exec.Command(binary, row.args...)
		output, _ := command.Output()
		if command.ProcessState == nil || command.ProcessState.ExitCode() != row.exit || string(output) != row.output {
			t.Errorf("CLI %v: output=%q state=%v, want %q / %d", row.args, output, command.ProcessState, row.output, row.exit)
		}
	}
	for _, args := range [][]string{{}, {"1", "2"}, {"x", "2", "1"}, {"1", "2", "9223372036854775808"}} {
		command := exec.Command(binary, args...)
		output, _ := command.CombinedOutput()
		if command.ProcessState == nil || command.ProcessState.ExitCode() != 2 || len(output) == 0 {
			t.Errorf("invalid CLI %v: output=%q state=%v", args, output, command.ProcessState)
		}
	}
}
