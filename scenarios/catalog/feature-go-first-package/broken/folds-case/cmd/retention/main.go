package main

import (
	"fmt"
	"os"

	"example.test/scenario/policy"
)

func main() {
	if len(os.Args) > 2 {
		fmt.Fprintln(os.Stderr, "usage: retention [TLD]")
		os.Exit(2)
	}
	tld := ""
	if len(os.Args) == 2 {
		tld = os.Args[1]
	}
	fmt.Println(policy.RetentionDays(tld))
}
