// Command policy prints the retention days for a top-level domain.
package main

import (
	"fmt"
	"os"
)

// RetentionDays mirrors reference/Policy.cs: exact, case-sensitive matches.
func RetentionDays(tld string) int {
	switch tld {
	case "de":
		return 7
	case "exception":
		return 1
	default:
		return 30
	}
}

func main() {
	tld := ""
	if len(os.Args) > 1 {
		tld = os.Args[1]
	}
	fmt.Println(RetentionDays(tld))
}
