package window_test

import (
	"testing"

	"example.test/scenario/window"
)

func TestC1ExcludesUpperEndpoint(t *testing.T) {
	if window.Contains(10, 20, 20) {
		t.Fatal("[10, 20) must exclude its upper endpoint")
	}
}

func TestC2EmptyInterval(t *testing.T) {
	if window.Contains(7, 7, 7) {
		t.Fatal("[7, 7) must contain no value")
	}
}
