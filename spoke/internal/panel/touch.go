package panel

import (
	"context"
	"encoding/binary"
	"os"
	"unsafe"
)

// Touch turns the panel's touches (an evdev device at Path) into the screen's.
type Touch struct {
	Path           string
	Rotate180      bool
	Size           int // the screen's: touches are scaled to it
	Down, Move, Up func(x, y int)
}

const (
	evSyn, evKey, evAbs = 0, 1, 3
	absX, absY          = 0x00, 0x01
	absMTX, absMTY      = 0x35, 0x36 // the multi-touch ones, which some drivers send instead
	absMTTrackingID     = 0x39
	btnTouch            = 0x14A
)

// eventSize is struct input_event's: a timeval (two longs), then its type, code and value.
var (
	longSize  = int(unsafe.Sizeof(uintptr(0)))
	eventSize = 2*longSize + 8
)

// axis is a touch axis's range, as the driver reports it.
type axis struct{ min, max int }

// finger is what the touches add up to so far.
type finger struct {
	x, y        int
	down, shown bool // touching now; touching as of the last report
	sentX       int
	sentY       int
}

func (t *Touch) Run(ctx context.Context) error {
	dev, err := os.Open(t.Path)
	if err != nil {
		return err
	}
	go func() { <-ctx.Done(); dev.Close() }() // the read below returns then
	ax, ay := axisRange(dev, absX, t.Size), axisRange(dev, absY, t.Size)
	var f finger
	buf := make([]byte, eventSize*64)
	for {
		n, err := dev.Read(buf)
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return err
		}
		t.feed(&f, ax, ay, buf[:n-n%eventSize])
	}
}

// feed reads events, and reports a touch at each report (EV_SYN): down, a move, or up.
func (t *Touch) feed(f *finger, ax, ay axis, events []byte) {
	for at := 0; at+eventSize <= len(events); at += eventSize {
		e := events[at+2*longSize:]
		kind, code := binary.LittleEndian.Uint16(e), binary.LittleEndian.Uint16(e[2:])
		value := int32(binary.LittleEndian.Uint32(e[4:]))
		switch {
		case kind == evAbs && (code == absX || code == absMTX):
			f.x = int(value)
		case kind == evAbs && (code == absY || code == absMTY):
			f.y = int(value)
		case kind == evAbs && code == absMTTrackingID:
			f.down = value >= 0
		case kind == evKey && code == btnTouch:
			f.down = value != 0
		case kind == evSyn:
			t.report(f, ax, ay)
		}
	}
}

func (t *Touch) report(f *finger, ax, ay axis) {
	x, y := scale(f.x, ax, t.Size), scale(f.y, ay, t.Size)
	if t.Rotate180 {
		x, y = t.Size-1-x, t.Size-1-y
	}
	switch {
	case f.down && !f.shown:
		t.Down(x, y)
	case f.down && (x != f.sentX || y != f.sentY):
		t.Move(x, y)
	case !f.down && f.shown:
		t.Up(f.sentX, f.sentY) // where it was last: an up's own place isn't always sent
	}
	if f.down {
		f.sentX, f.sentY = x, y
	}
	f.shown = f.down
}

// scale maps a driver's value into the screen, 0 to size-1.
func scale(v int, a axis, size int) int {
	if a.max <= a.min {
		return min(max(v, 0), size-1)
	}
	return min(max((v-a.min)*(size-1)/(a.max-a.min), 0), size-1)
}
