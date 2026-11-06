package elevate

import (
	"fmt"
	"os/exec"
	"strings"
	"syscall"
)

// Run runs exe with args as admin: Windows asks for permission first. It returns when the command
// has finished, with an error if it failed.
func Run(exe string, args ...string) error {
	quote := func(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" } // for PowerShell
	list := make([]string, len(args))
	for i, a := range args {
		list[i] = quote(`"` + strings.ReplaceAll(a, `"`, `\"`) + `"`) // each one quoted for the program too
	}
	script := fmt.Sprintf(`try { $p = Start-Process -FilePath %s -ArgumentList @(%s) -Verb RunAs -Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode } catch { Write-Output $_.Exception.Message; exit 1223 }`,
		quote(exe), strings.Join(list, ", "))
	cmd := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	out, err := cmd.CombinedOutput()
	if err == nil {
		return nil
	}
	if exit, ok := err.(*exec.ExitError); ok && (exit.ExitCode() == 1223 || strings.Contains(string(out), "canceled")) {
		return ErrCanceled
	}
	return fmt.Errorf("writing the card: %s", strings.TrimSpace(string(out)))
}
