package flash

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	"github.com/Cinderwire-co/TouchStream/installer/internal/elevate"
	"github.com/Cinderwire-co/TouchStream/installer/internal/image"
)

func TestACardIsWrittenThenChecked(t *testing.T) {
	dir := t.TempDir()
	img := bytes.Repeat([]byte{7}, 6<<20+512)
	copy(img[1<<20:], image.Magic)
	clear(img[1<<20+len(image.Magic) : 1<<20+image.BlockSize])
	os.WriteFile(filepath.Join(dir, "spoke.img"), img, 0o644)
	card, _ := os.Create(filepath.Join(dir, "card"))
	defer card.Close()

	var phases []string
	p := &image.Provision{HubURL: "http://hub:8080", SpokeID: "id", Token: "t", Name: "Hall"}
	err := Card(card, filepath.Join(dir, "spoke.img"), p, func(pr elevate.Progress) {
		if len(phases) == 0 || phases[len(phases)-1] != pr.Phase {
			phases = append(phases, pr.Phase)
		}
	})
	if err != nil {
		t.Fatal(err)
	}
	if got := len(phases); got != 3 || phases[0] != "writing" || phases[1] != "checking" || phases[2] != "done" {
		t.Fatalf("phases %v", phases)
	}
	written, _ := os.ReadFile(filepath.Join(dir, "card"))
	settings, err := image.Parse(written[1<<20:])
	if err != nil || settings.SpokeID != "id" {
		t.Fatalf("settings on the card: %+v %v", settings, err)
	}
}
