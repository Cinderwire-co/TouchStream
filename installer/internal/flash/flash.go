// Package flash writes one card: the image (with the spoke's settings filled in), then every byte
// read back and checked. It runs in the copy of the installer that has admin rights, or in the
// app itself for a disk image a developer can write anyway.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package flash

import (
	"errors"
	"io"
	"os"
	"syscall"
	"time"

	"github.com/Cinderwire-co/TouchStream/installer/internal/cards"
	"github.com/Cinderwire-co/TouchStream/installer/internal/elevate"
	"github.com/Cinderwire-co/TouchStream/installer/internal/image"
)

// Card writes the image at imagePath to target with p's settings, and checks it, reporting as it
// goes (at most five times a second, and always the end).
func Card(target cards.Target, imagePath string, p *image.Provision, report func(elevate.Progress)) error {
	src, err := os.Open(imagePath)
	if err != nil {
		return err
	}
	defer src.Close()
	info, err := src.Stat()
	if err != nil {
		return err
	}
	total := image.Padded(info.Size())
	var last time.Time
	tell := func(phase string, done int64) {
		if now := time.Now(); now.Sub(last) > 200*time.Millisecond || done == total {
			last = now
			report(elevate.Progress{Phase: phase, Done: done, Total: total})
		}
	}
	tell("writing", 0)
	res, err := image.Write(target, src, p, func(done int64) { tell("writing", done) })
	if err != nil {
		return err
	}
	if f, ok := target.(interface{ Sync() error }); ok {
		// A Mac's raw disk (/dev/rdisk) has no cache to flush, and says so
		if err := f.Sync(); err != nil && !errors.Is(err, syscall.ENOTTY) && !errors.Is(err, syscall.ENOTSUP) {
			return err
		}
	}
	if p != nil && !res.Provisioned {
		// An image without a settings block: the box will show up under Add spoke instead
		report(elevate.Progress{Phase: "writing", Done: total, Total: total, Error: "no-settings-block"})
	}
	last = time.Time{}
	tell("checking", 0)
	err = image.Verify(io.NewSectionReader(target, 0, res.Written), res.Written, res.Sum, func(done int64) { tell("checking", done) })
	if err != nil {
		return err
	}
	report(elevate.Progress{Phase: "done", Done: total, Total: total})
	return nil
}

// ErrTooSmall: the card is smaller than the image.
var ErrTooSmall = errors.New("this card is too small for the spoke: use one of 32 GB or more")
