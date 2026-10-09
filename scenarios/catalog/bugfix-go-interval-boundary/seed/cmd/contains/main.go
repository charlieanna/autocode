package main

import (
	"fmt"
	"os"
	"strconv"

	"example.test/scenario/window"
)

func main() {
	if len(os.Args) != 4 {
		fmt.Fprintln(os.Stderr, "usage: contains START END VALUE")
		os.Exit(2)
	}
	var values [3]int64
	for index, argument := range os.Args[1:] {
		value, err := strconv.ParseInt(argument, 10, 64)
		if err != nil {
			fmt.Fprintln(os.Stderr, "endpoints and value must be int64 integers")
			os.Exit(2)
		}
		values[index] = value
	}
	contained := window.Contains(values[0], values[1], values[2])
	fmt.Println(contained)
	if !contained {
		os.Exit(1)
	}
}
