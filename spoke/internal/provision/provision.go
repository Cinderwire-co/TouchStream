// Package provision reads the settings the installer writes on a spoke's card: which hub it
// pairs with and what it's called, so a new box shows up in the dashboard by itself.
//
// The spoke's image has a 64 KiB block, aligned to 64 KiB within its first 64 MiB, starting
// with Magic. The installer (installer/internal/image) replaces it with Magic, the settings as
// JSON and a newline, then zeros.
package provision

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"strings"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/config"
)

const (
	Magic     = "TOUCHSTREAM-PROVISION-V1\n"
	blockSize = 64 << 10
	searchIn  = 64 << 20
)

// Devices are where the box's card might be (it's checked at bring-up which one it is).
var Devices = []string{"/dev/mmcblk1", "/dev/mmcblk0"}

type Settings struct {
	HubURL  string `json:"hub_url,omitempty"`
	SpokeID string `json:"spoke_id,omitempty"`
	Token   string `json:"token,omitempty"`
	Name    string `json:"name,omitempty"`
}

var ErrNone = errors.New("no settings block")

// Read finds the settings block in r.
func Read(r io.Reader) (*Settings, error) {
	block := make([]byte, blockSize)
	for at := 0; at < searchIn; at += blockSize {
		if _, err := io.ReadFull(r, block); err != nil {
			return nil, ErrNone
		}
		if !bytes.HasPrefix(block, []byte(Magic)) {
			continue
		}
		line, _, _ := bytes.Cut(block[len(Magic):], []byte{'\n'})
		if len(bytes.Trim(line, "\x00")) == 0 {
			return nil, ErrNone // as the image is built: written some other way than with the installer
		}
		var s Settings
		if err := json.Unmarshal(line, &s); err != nil {
			return nil, err
		}
		return &s, nil
	}
	return nil, ErrNone
}

// Apply takes the card's settings, once: a box unpaired later doesn't pair again on its next
// start. It says what it did, or "" if there was nothing new.
func Apply(store *config.Store, devices []string) (string, error) {
	for _, device := range devices {
		f, err := os.Open(device)
		if err != nil {
			continue
		}
		s, err := Read(f)
		f.Close()
		if errors.Is(err, ErrNone) {
			continue
		}
		if err != nil {
			return "", err
		}
		key := strings.TrimSpace(s.SpokeID + " " + s.Name)
		if key == "" || store.Get().Provisioned == key {
			return "", nil
		}
		err = store.Update(func(c *config.Config) {
			if s.Name != "" {
				c.DeviceName = s.Name
			}
			if s.HubURL != "" && s.SpokeID != "" && s.Token != "" {
				c.HubURL, c.SpokeID, c.HubToken = s.HubURL, s.SpokeID, s.Token
			}
			c.Provisioned = key
		})
		if err != nil {
			return "", err
		}
		if s.SpokeID != "" {
			return "paired with the hub at " + s.HubURL + ", from the card", nil
		}
		return "named " + s.Name + ", from the card", nil
	}
	return "", nil
}
