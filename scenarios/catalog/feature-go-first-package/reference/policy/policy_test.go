package policy_test

import (
	"testing"

	"example.test/scenario/policy"
)

func TestC1GermanRetention(t *testing.T) {
	if got := policy.RetentionDays("de"); got != 7 {
		t.Fatalf("RetentionDays(de) = %d, want 7", got)
	}
}

func TestC2ExceptionRetention(t *testing.T) {
	if got := policy.RetentionDays("exception"); got != 1 {
		t.Fatalf("RetentionDays(exception) = %d, want 1", got)
	}
}
