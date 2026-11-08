package hubsetup

import (
	"os/exec"
	"syscall"
)

// hideWindow: the script's PowerShell window stays hidden; its output shows in the installer.
func hideWindow(cmd *exec.Cmd) { cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true} }
