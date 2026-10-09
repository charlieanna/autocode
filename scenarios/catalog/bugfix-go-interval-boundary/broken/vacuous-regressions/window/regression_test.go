package window_test

import (
	"example.test/scenario/window"
	"testing"
)

func TestC1ExcludesUpperEndpoint(t *testing.T) {
	if !window.Contains(10, 20, 15) {
		t.Fatal("an interior point must remain contained")
	}
}

func TestC2EmptyInterval(t *testing.T) {
	if window.Contains(7, 7, 8) {
		t.Fatal("an outside point must remain excluded")
	}
}
