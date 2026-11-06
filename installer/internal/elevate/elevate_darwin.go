package elevate

import (
	"fmt"
	"os/exec"
	"strings"
)

// Run runs exe with args as admin: macOS asks for an admin's password first. It returns when the
// command has finished.
func Run(exe string, args ...string) error {
	quoted := make([]string, 0, len(args)+1)
	for _, a := range append([]string{exe}, args...) {
		quoted = append(quoted, "'"+strings.ReplaceAll(a, "'", `'\''`)+"'") // for the shell
	}
	command := strings.Join(quoted, " ")
	script := fmt.Sprintf(`do shell script "%s" with prompt "TouchStream needs your password to write the spoke's card." with administrator privileges`,
		strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(command)) // for AppleScript
	out, err := exec.Command("osascript", "-e", script).CombinedOutput()
	if err != nil {
		if strings.Contains(string(out), "-128") || strings.Contains(string(out), "User canceled") {
			return ErrCanceled
		}
		return fmt.Errorf("writing the card: %s", strings.TrimSpace(string(out)))
	}
	return nil
}
