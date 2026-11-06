package hubsetup

import (
	"bytes"
	"os"
	"testing"
)

// The scripts here are copies of the hub's own: they mustn't drift apart.
func TestTheScriptsMatchTheHubs(t *testing.T) {
	for _, name := range []string{"macos.sh", "windows.ps1"} {
		mine, err := scripts.ReadFile("scripts/" + name)
		if err != nil {
			t.Fatal(err)
		}
		theirs, err := os.ReadFile("../../../hub/install/" + name)
		if err != nil {
			t.Skip("not in the TouchStream repo")
		}
		if !bytes.Equal(mine, theirs) {
			t.Errorf("internal/hubsetup/scripts/%s differs from hub/install/%s: copy it over", name, name)
		}
	}
}
