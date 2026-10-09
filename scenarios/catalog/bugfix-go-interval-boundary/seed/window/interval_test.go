package window_test

import (
	"testing"

	"example.test/scenario/window"
)

func TestExistingInterior(t *testing.T) {
	if !window.Contains(10, 20, 15) {
		t.Fatal("interior value must be contained")
	}
}

func TestExistingOutside(t *testing.T) {
	for _, value := range []int64{9, 21} {
		if window.Contains(10, 20, value) {
			t.Fatalf("outside value %d must not be contained", value)
		}
	}
}
