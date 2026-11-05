// Package screen draws the PicoKVM's 1.54" touchscreen (240×240): what the spoke is doing at
// a glance, and a Record button, like the Pi spoke's touchscreen (spoke/touchstream/ui).
//
//	┌──────────────────────────┐
//	│ ◆ Auditorium           ● │  name, hub connection
//	│                          │
//	│         [ LIVE ]         │  off / preview / live, or set up
//	│     1080p30 · HDMI       │  the input
//	│     Sending to hub       │  the stream, or what to do next
//	│                          │
//	│ ● REC               (■)  │  recording time, Record / Stop
//	└──────────────────────────┘
//
// In the emulator it's served as a picture you can click (Routes); on the box it goes to the
// panel's framebuffer (still to come).
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package screen

import (
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"strings"
	"sync"
	"time"

	"github.com/Cinderwire-co/TouchStream/lite/internal/recorder"
	"github.com/Cinderwire-co/TouchStream/lite/internal/spoke"
)

const (
	Size      = 240
	armFor    = 3 * time.Second // stopping takes a second tap within this, so a stray touch can't
	buttonX   = 196
	buttonY   = 198
	buttonR   = 28
	tapMargin = 10
)

type Screen struct {
	State    func() spoke.State
	Record   func() bool                        // starts or stops a recording made here
	Sessions func() []recorder.Session          // the recordings on the card
	Share    func(name string) string           // a link to a recording's phone page
	Prepared func(name string) (string, string) // how its phone copy is coming along

	mu      sync.Mutex
	armed   time.Time // a first tap on Stop, waiting for the second
	view    view
	scroll  int // how far the recordings list is scrolled
	touch   *touch
	chosen  recorder.Session // the recording whose QR code is showing
	link    string
	touched time.Time
}

type view int

const (
	home   view = iota
	list        // the recordings on the card
	detail      // one recording's QR code
)

// A finger on the screen: where it went down, and whether it has moved enough to be a drag.
type touch struct {
	x, y, scroll int
	dragging     bool
}

const (
	dragAfter = 8           // px a finger moves before a touch becomes a drag
	idleFor   = time.Minute // the list and QR code go back home after this without a touch
)

// canRecord: Record shows while there's a picture to record and the hub isn't already recording it.
func canRecord(st spoke.State) bool {
	return st.Manual || (st.Mode != "live" && st.Signal.Present)
}

// Down, Move and Up are a finger on the screen; a touch that doesn't move is a tap.
func (s *Screen) Down(x, y int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.touch, s.touched = &touch{x: x, y: y, scroll: s.scroll}, time.Now()
}

func (s *Screen) Move(x, y int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	t := s.touch
	if t == nil {
		return
	}
	if abs(y-t.y) > dragAfter || abs(x-t.x) > dragAfter {
		t.dragging = true
	}
	if t.dragging && s.view == list {
		s.scrollTo(t.scroll - (y - t.y))
	}
}

func (s *Screen) Up(x, y int) {
	s.mu.Lock()
	t := s.touch
	s.touch = nil
	s.mu.Unlock()
	if t != nil && !t.dragging {
		s.tap(x, y)
	}
}

// Tap is a touch that goes down and up in one place.
func (s *Screen) Tap(x, y int) {
	s.Down(x, y)
	s.Up(x, y)
}

// Wheel scrolls the list by dy px (the emulator's mouse wheel).
func (s *Screen) Wheel(dy int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.view == list {
		s.scrollTo(s.scroll + dy)
		s.touched = time.Now()
	}
}

func (s *Screen) tap(x, y int) {
	s.mu.Lock()
	v := s.view
	s.mu.Unlock()
	switch v {
	case home:
		s.tapHome(x, y)
	case list:
		s.tapList(x, y)
	case detail:
		if inBack(x, y) {
			s.show(list)
		}
	}
}

func (s *Screen) tapHome(x, y int) {
	st := s.State()
	dx, dy := x-buttonX, y-buttonY
	if canRecord(st) && dx*dx+dy*dy <= (buttonR+tapMargin)*(buttonR+tapMargin) {
		s.mu.Lock()
		defer s.mu.Unlock()
		if st.Manual && time.Since(s.armed) > armFor {
			s.armed = time.Now() // the first tap on Stop only arms it
			return
		}
		s.armed = time.Time{}
		s.Record()
		return
	}
	if y >= 50 && y <= 170 && s.Sessions != nil { // the middle: the recordings
		s.mu.Lock()
		s.view, s.scroll = list, 0
		s.mu.Unlock()
	}
}

// Link is the phone page the QR code on screen points at, if one is showing.
func (s *Screen) Link() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.view != detail {
		return ""
	}
	return s.link
}

func abs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

func (s *Screen) Render(now time.Time) *image.RGBA {
	s.mu.Lock()
	if s.view != home && now.Sub(s.touched) > idleFor {
		s.view, s.scroll = home, 0
	}
	v := s.view
	s.mu.Unlock()
	img := image.NewRGBA(image.Rect(0, 0, Size, Size))
	draw.Draw(img, img.Bounds(), image.NewUniform(ink), image.Point{}, draw.Src)
	switch v {
	case list:
		s.renderList(img, now)
	case detail:
		s.renderDetail(img)
	default:
		s.renderHome(img, now)
	}
	return img
}

func (s *Screen) renderHome(img *image.RGBA, now time.Time) {
	st := s.State()
	s.mu.Lock()
	armed := !s.armed.IsZero() && now.Sub(s.armed) <= armFor
	s.mu.Unlock()

	// Name and hub connection
	draw.Draw(img, image.Rect(12, 9, 34, 31), markPx, image.Point{}, draw.Over)
	name := st.Name
	if name == "" {
		name = "TouchStream"
	}
	text(img, title, fit(title, name, 170), 42, 26, -1, ash)
	dot := faint
	if st.Paired {
		dot = bad
		if st.HubOK {
			dot = ok
		}
	}
	fill(img, dot, circle(225, 20, 5))

	// What it's doing
	line1, line2 := signalLine(st), streamLine(st)
	switch {
	case !st.Paired:
		headline(img, "SET UP", ash, false)
		line2 = "Hub › Add spoke › " + st.IP
	case st.Mode == "live":
		headline(img, "LIVE", rec, true)
	case st.Mode == "preview":
		headline(img, "PREVIEW", ash, false)
	case st.Recording:
		headline(img, "RECORDING", rec, false)
	case !st.Signal.Present:
		headline(img, "NO SIGNAL", smoke, false)
	default:
		headline(img, "READY", ash, false)
	}
	if armed {
		line2 = "Tap again to stop"
	}
	text(img, mono, fit(mono, line1, 220), Size/2, 136, 0, signalColor(st))
	text(img, small, fit(small, line2, 224), Size/2, 157, 0, smoke)

	// Recording, and the Record button
	if st.Recording {
		fill(img, rec, circle(20, 189, 5))
		text(img, label, "REC", 31, 194, -1, rec)
		text(img, timer, clock(now.Sub(st.RecordedAt)), 14, 222, -1, ash)
	} else if canRecord(st) {
		text(img, label, "Record", 14, 204, -1, smoke)
	}
	if canRecord(st) {
		ring := ash
		if armed {
			ring = rec
		}
		fill(img, ring, circle(buttonX, buttonY, buttonR))
		fill(img, ink, circle(buttonX, buttonY, buttonR-3))
		if st.Manual {
			fill(img, rec, roundRect(buttonX-11, buttonY-11, buttonX+11, buttonY+11, 4))
		} else {
			fill(img, rec, circle(buttonX, buttonY, buttonR-8))
		}
	}
}

// headline is the big status word, in a pill: filled for live, outlined otherwise.
func headline(img *image.RGBA, word string, col color.Color, solid bool) {
	f := big
	if textWidth(f, word) > 180 {
		f = medium // RECORDING
	}
	w := textWidth(f, word)
	x0, x1 := float32(Size/2-w/2-18), float32(Size/2+w/2+18)
	const y0, y1 = 62, 112
	if solid {
		fill(img, col, roundRect(x0, y0, x1, y1, 25))
		text(img, f, word, Size/2, 87+f.Metrics().CapHeight.Ceil()/2, 0, ash)
		return
	}
	fill(img, charcoal, roundRect(x0, y0, x1, y1, 25))
	text(img, f, word, Size/2, 87+f.Metrics().CapHeight.Ceil()/2, 0, col)
}

func signalLine(st spoke.State) string {
	if !st.Signal.Present {
		return "Connect a source to HDMI IN"
	}
	fps := strings.TrimRight(strings.TrimRight(fmt.Sprintf("%.2f", st.Signal.FPS), "0"), ".")
	return fmt.Sprintf("%dp%s · HDMI", st.Signal.Height, fps)
}

func signalColor(st spoke.State) color.Color {
	if st.Signal.Present {
		return ash
	}
	return smoke
}

func streamLine(st spoke.State) string {
	switch {
	case st.Uploading != "":
		return "Uploading to hub · " + st.Uploading
	case st.Mode == "off" || st.Mode == "":
		if st.HubOK {
			return "Hub connected · " + st.IP
		}
		return "Hub unreachable · " + st.IP
	}
	switch st.Uplink {
	case "sending":
		return "Sending to hub"
	case "retrying":
		return "Can't reach the hub · retrying"
	}
	return "Connecting to hub…"
}

func clock(d time.Duration) string {
	s := int(d.Seconds())
	return fmt.Sprintf("%d:%02d:%02d", s/3600, s/60%60, s%60)
}
