//go:build !windows

package main

import (
	"os"
	"os/exec"
	"strconv"
)

// keepAwake keeps the Mac from sleeping until this process ends (writing a card takes minutes).
func keepAwake() {
	_ = exec.Command("caffeinate", "-i", "-w", strconv.Itoa(os.Getpid())).Start()
}
