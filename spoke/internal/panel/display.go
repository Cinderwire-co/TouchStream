// Package panel puts the spoke's screen on the PicoKVM's own: its ST7789V, a Linux framebuffer
// (/dev/fb0, 240×240, 16-bit), and its CST816S touch (evdev, /dev/input/event0). In the case,
// both are turned 180° (as Luckfox's own screen app has them).
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package panel

import (
	"bytes"
	"context"
	"image"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Backlight is the panel's backlight, and the level Luckfox's software sets it to.
const (
	Backlight      = "/sys/class/backlight/backlight"
	BacklightLevel = 64
)

// Display draws the screen on the framebuffer at Path, whenever it changes: 20 times a second while
// something's moving, 4 times a second while nothing is (the box has one core), and at once on a touch.
type Display struct {
	Path      string
	Rotate180 bool
	Render    func(time.Time) *image.RGBA

	once sync.Once
	wake chan struct{}
}

// Wake draws the screen again now (after a touch), at full speed.
func (d *Display) Wake() {
	select {
	case d.woken() <- struct{}{}:
	default:
	}
}

func (d *Display) woken() chan struct{} {
	d.once.Do(func() { d.wake = make(chan struct{}, 1) })
	return d.wake
}

// Format is how the framebuffer lays out its pixels.
type Format struct {
	Width, Height, Stride, Bits int
	Red, Green, Blue            Channel
}

// Channel is where a color's bits go in a pixel.
type Channel struct{ Offset, Length int }

// RGB565 is what the PicoKVM's panel takes, if the framebuffer can't be asked (a file, in a test).
var RGB565 = Format{Width: 240, Height: 240, Stride: 480, Bits: 16,
	Red: Channel{11, 5}, Green: Channel{5, 6}, Blue: Channel{0, 5}}

func (d *Display) Run(ctx context.Context) error {
	fb, err := os.OpenFile(d.Path, os.O_RDWR, 0)
	if err != nil {
		return err
	}
	defer fb.Close()
	format, err := query(fb)
	if err != nil {
		format = RGB565
	}
	backlight(Backlight)
	frame := make([]byte, format.Stride*format.Height)
	last := make([]byte, len(frame))
	const moving, still = 50 * time.Millisecond, 250 * time.Millisecond
	timer := time.NewTimer(moving)
	defer timer.Stop()
	same := 0 // frames in a row that came out as the last did
	for first := true; ; first = false {
		Pack(format, d.Render(time.Now()), d.Rotate180, frame)
		if first || !bytes.Equal(frame, last) {
			if _, err := fb.WriteAt(frame, 0); err != nil {
				return err
			}
			copy(last, frame)
			same = 0
		} else {
			same++
		}
		wait := moving
		if same >= 3 {
			wait = still
		}
		timer.Reset(wait)
		select {
		case <-ctx.Done():
			return nil
		case <-timer.C:
		case <-d.woken():
			same = 0
		}
	}
}

// Show puts one picture on the framebuffer at path, with the backlight on (the splash, at startup).
func Show(path string, img *image.RGBA, rotate bool) error {
	fb, err := os.OpenFile(path, os.O_RDWR, 0)
	if err != nil {
		return err
	}
	defer fb.Close()
	format, err := query(fb)
	if err != nil {
		format = RGB565
	}
	frame := make([]byte, format.Stride*format.Height)
	Pack(format, img, rotate, frame)
	if _, err := fb.WriteAt(frame, 0); err != nil {
		return err
	}
	backlight(Backlight)
	return nil
}

// Pack lays img out as the framebuffer wants it, into out (Stride × Height bytes).
func Pack(f Format, img *image.RGBA, rotate bool, out []byte) {
	b := img.Bounds()
	size := f.Bits / 8
	if size == 2 && b.Dx() >= f.Width && b.Dy() >= f.Height { // the panel's 16 bits: row by row, quickly
		for y := 0; y < f.Height; y++ {
			sy, step, sx := y, 4, 0
			if rotate {
				sy, step, sx = f.Height-1-y, -4, 4*(f.Width-1)
			}
			src := img.Pix[img.PixOffset(b.Min.X, b.Min.Y+sy):]
			row := out[y*f.Stride : y*f.Stride+2*f.Width]
			for x := 0; x < len(row); x, sx = x+2, sx+step {
				v := uint32(src[sx])>>(8-f.Red.Length)<<f.Red.Offset | uint32(src[sx+1])>>(8-f.Green.Length)<<f.Green.Offset |
					uint32(src[sx+2])>>(8-f.Blue.Length)<<f.Blue.Offset
				row[x], row[x+1] = byte(v), byte(v>>8)
			}
		}
		return
	}
	for y := 0; y < f.Height; y++ {
		for x := 0; x < f.Width; x++ {
			sx, sy := x, y
			if rotate {
				sx, sy = f.Width-1-x, f.Height-1-y
			}
			var r, g, bl uint32
			if sx < b.Dx() && sy < b.Dy() {
				i := img.PixOffset(b.Min.X+sx, b.Min.Y+sy)
				r, g, bl = uint32(img.Pix[i]), uint32(img.Pix[i+1]), uint32(img.Pix[i+2])
			}
			v := r>>(8-f.Red.Length)<<f.Red.Offset | g>>(8-f.Green.Length)<<f.Green.Offset |
				bl>>(8-f.Blue.Length)<<f.Blue.Offset
			at := y*f.Stride + x*size
			for k := 0; k < size; k++ { // little-endian, as the framebuffer has it
				out[at+k] = byte(v >> (8 * k))
			}
		}
	}
}

// backlight turns the panel's backlight on, at Luckfox's level (or its most, if that's less).
func backlight(dir string) {
	level := BacklightLevel
	if data, err := os.ReadFile(filepath.Join(dir, "max_brightness")); err == nil {
		if most, err := strconv.Atoi(strings.TrimSpace(string(data))); err == nil && most < level {
			level = most
		}
	}
	_ = os.WriteFile(filepath.Join(dir, "brightness"), []byte(strconv.Itoa(level)), 0)
}
