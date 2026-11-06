package provision

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/config"
)

// card is a card as the installer leaves it: the settings block 8 MiB in.
func card(t *testing.T, settings string) string {
	data := make([]byte, 10<<20)
	copy(data[8<<20:], Magic+settings+"\n")
	path := filepath.Join(t.TempDir(), "card.img")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestTheCardPairsTheSpokeOnce(t *testing.T) {
	store, err := config.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	path := card(t, `{"hub_url":"http://192.168.1.20:8080","spoke_id":"ab12cd34","token":"secret","name":"Chapel"}`)
	did, err := Apply(store, []string{"/dev/not-here", path})
	if err != nil || did == "" {
		t.Fatalf("applied %q, %v", did, err)
	}
	c := store.Get()
	if !c.Paired() || c.HubURL != "http://192.168.1.20:8080" || c.SpokeID != "ab12cd34" || c.HubToken != "secret" || c.DeviceName != "Chapel" {
		t.Fatalf("config %+v", c)
	}
	// Unpaired on the box later: the card doesn't pair it again
	_ = store.Update(func(c *config.Config) { c.HubURL, c.SpokeID, c.HubToken = "", "", "" })
	if did, _ := Apply(store, []string{path}); did != "" || store.Get().Paired() {
		t.Fatalf("applied again: %q", did)
	}
}

func TestACardWithoutAHub(t *testing.T) {
	store, _ := config.Open(t.TempDir())
	if did, err := Apply(store, []string{card(t, `{"name":"Gym"}`)}); err != nil || did == "" {
		t.Fatalf("applied %q, %v", did, err)
	}
	if c := store.Get(); c.Paired() || c.DeviceName != "Gym" {
		t.Fatalf("config %+v", c)
	}
}

func TestAnImageNobodyProvisioned(t *testing.T) {
	// The block as the image is built: Magic and zeros
	if s, err := Read(bytes.NewReader(append([]byte(Magic), make([]byte, blockSize)...))); err != ErrNone {
		t.Fatalf("read %+v, %v from an empty block", s, err)
	}
	if _, err := Read(bytes.NewReader(make([]byte, 1<<20))); err != ErrNone {
		t.Fatalf("no block: %v", err)
	}
}
