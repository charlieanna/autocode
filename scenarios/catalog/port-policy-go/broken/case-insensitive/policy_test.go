package main

import (
	"encoding/json"
	"os"
	"testing"
)

func TestGoldenCases(t *testing.T) {
	raw, err := os.ReadFile("golden-cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Input string `json:"input"`
		Days  int    `json:"days"`
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) != 8 {
		t.Fatalf("want 8 golden cases, got %d", len(cases))
	}
	for _, c := range cases {
		if got := RetentionDays(c.Input); got != c.Days {
			t.Errorf("RetentionDays(%q) = %d, want %d", c.Input, got, c.Days)
		}
	}
}
