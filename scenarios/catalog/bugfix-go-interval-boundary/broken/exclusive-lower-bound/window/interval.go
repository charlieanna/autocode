package window

// Contains reports membership in the interval.
func Contains(start, end, value int64) bool {
	return start < value && value < end
}
