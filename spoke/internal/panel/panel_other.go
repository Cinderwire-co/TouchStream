//go:build !linux

package panel

import (
	"errors"
	"os"
)

// query: only Linux has framebuffers to ask (elsewhere, a file stands in for one, as RGB565).
func query(*os.File) (Format, error) { return Format{}, errors.New("no framebuffer here") }

// axisRange: only Linux has touch drivers to ask; elsewhere touches are the screen's own size.
func axisRange(_ *os.File, _ uintptr, size int) axis { return axis{0, size - 1} }
