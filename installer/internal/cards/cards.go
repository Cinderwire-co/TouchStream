// Package cards finds the microSD cards in this computer, and gets one ready to write. It only
// ever offers removable media: never the computer's own disks, whatever they're called.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package cards

import (
	"errors"
	"fmt"
	"io"
	"sort"
)

// MaxSize: a card bigger than this is more likely a backup drive, so it isn't offered.
const MaxSize = 512 << 30

type Card struct {
	ID       string   `json:"id"`     // disk4 (macOS) or 3 (Windows' disk number)
	Device   string   `json:"device"` // what's written to: /dev/rdisk4, \\.\PhysicalDrive3
	Name     string   `json:"name"`   // the reader or card's name, e.g. "Apple SDXC Reader Media"
	Bus      string   `json:"bus"`    // SD, USB, ...
	Size     int64    `json:"size"`
	ReadOnly bool     `json:"read_only"` // its lock switch is on
	Virtual  bool     `json:"virtual"`   // a disk image (developer mode only)
	Volumes  []string `json:"volumes"`   // what's on it now, to show before it's erased
}

// Disk is what the system says about one disk, before deciding whether it could be a card.
type Disk struct {
	Card
	Internal    bool
	Removable   bool
	System      bool // the computer starts from it, or it holds the system
	Synthesized bool // built by the system on top of another disk (an APFS container): never a card
}

var cardBuses = map[string]bool{"SD": true, "Secure Digital": true, "USB": true, "MMC": true}

// Offer picks the disks that could be a card to write: removable, not the system's, at most
// MaxSize, with media in. Disk images only when virtual is true (developer mode).
func Offer(disks []Disk, virtual bool) []Card {
	var out []Card
	for _, d := range disks {
		switch {
		case d.System, d.Synthesized, d.Size == 0, d.Size > MaxSize:
		case d.Virtual && virtual && d.Removable && !d.ReadOnly: // a disk image someone attached (developer mode)
			out = append(out, d.Card)
		case d.Virtual, !d.Removable, !cardBuses[d.Bus]:
		case d.Internal && d.Bus != "Secure Digital" && d.Bus != "SD": // a built-in SD slot is fine
		default:
			out = append(out, d.Card)
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return !out[i].Virtual && out[j].Virtual }) // real cards first
	return out
}

var (
	ErrGone     = errors.New("the card was taken out: put it back in and try again")
	ErrChanged  = errors.New("that's a different card now: choose it again")
	ErrReadOnly = errors.New("the card is locked: slide the switch on its side up, away from LOCK, and put it back in")
	ErrBusy     = errors.New("the card is in use: close any window showing what's on it, and try again")
)

// Target is a card opened to write: positions to write the image at, and the card to read back.
type Target interface {
	io.WriterAt
	io.ReaderAt
	io.Closer
}

// Same checks a card is still the one that was chosen (by its size and name) right before
// it's written: a card swapped in the meantime mustn't be erased by mistake.
func Same(chosen Card, now []Card) error {
	for _, c := range now {
		if c.ID == chosen.ID {
			if c.Size != chosen.Size || c.Name != chosen.Name {
				return ErrChanged
			}
			if c.ReadOnly {
				return ErrReadOnly
			}
			return nil
		}
	}
	return ErrGone
}

func sizeLabel(n int64) string {
	return fmt.Sprintf("%.1f GB", float64(n)/1e9)
}

// Label is how a card is shown: "Apple SDXC Reader Media · 31.9 GB".
func (c Card) Label() string { return c.Name + " · " + sizeLabel(c.Size) }
