package policy

// RetentionDays preserves the exact, case-sensitive legacy policy.
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
