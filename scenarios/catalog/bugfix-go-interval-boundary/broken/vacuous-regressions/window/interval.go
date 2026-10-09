package window

// Contains reports whether value lies in the half-open interval [start, end).
func Contains(start, end, value int64) bool {
	return start <= value && value < end
}
