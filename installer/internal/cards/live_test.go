package cards

import (
	"os"
	"testing"
)

// With CARDS_LIVE=1, list this computer's cards (developer mode) for a look.
func TestListThisComputer(t *testing.T) {
	if os.Getenv("CARDS_LIVE") == "" {
		t.Skip("set CARDS_LIVE=1")
	}
	cards, err := List(true)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range cards {
		t.Logf("%s %s %s writable=%v", c.ID, c.Device, c.Label(), Writable(c))
	}
}
