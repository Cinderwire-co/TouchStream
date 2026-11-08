//go:build !windows

package hubsetup

import "os/exec"

func hideWindow(*exec.Cmd) {}
