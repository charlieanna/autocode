package policy

import "strings"

func RetentionDays(tld string) int {
	switch strings.ToLower(tld) {
	case "de":
		return 7
	case "exception":
		return 1
	default:
		return 30
	}
}
