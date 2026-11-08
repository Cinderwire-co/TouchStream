package main

import "syscall"

// keepAwake keeps the PC from sleeping until this process ends (writing a card takes minutes).
func keepAwake() {
	const continuous, systemRequired = 0x80000000, 0x00000001
	_, _, _ = syscall.NewLazyDLL("kernel32.dll").NewProc("SetThreadExecutionState").Call(continuous | systemRequired)
}
