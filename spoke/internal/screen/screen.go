// Package screen draws the PicoKVM's 1.54" touchscreen (240×240): what the spoke is doing at
// a glance, and its controls.
//
//	┌──────────────────────────────┐
//	│ ◆ Auditorium               ● │  name, hub connection
//	│                              │
//	│           [ LIVE ]           │  off / preview / live, or set up
//	│        1080p30 · HDMI        │  the input
//	│   Live 0:12:34 · sending     │  the stream, a timer, or what to do next
//	│                              │
//	│ (Recordings) (End)       (●) │  the recordings, Go live / End, Record / Stop
//	└──────────────────────────────┘
//
// In the emulator it's served as a picture you can click (Routes); on the box it goes to the
// panel's framebuffer (still to come).
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package screen

import (
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"math"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/image/font"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/recorder"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/spoke"
)

const (
	Size      = 240
	armFor    = 3 * time.Second // stopping takes a second tap within this, so a stray touch can't
	tapMargin = 8
	noteFor   = 4 * time.Second
	// The buttons along the bottom, all with the same white ring: Recordings, Go live / End, Record / Stop
	buttonY  = 198
	buttonR  = 24
	ringW    = 3
	liveX    = 152
	buttonX  = 210 // Record
	pillLeft = 10
	pillEnd  = 120
)

type Screen struct {
	State    func() spoke.State
	Record   func() bool                        // starts or stops a recording made here
	Live     func(on bool) error                // goes live, or ends (spoke.ErrNoKeys: needs setup)
	Setup    func() string                      // the setup page's link
	Sessions func() []recorder.Session          // the recordings on the card
	Share    func(name string) string           // a link to a recording's phone page
	Prepared func(name string) (string, string) // how its phone copy is coming along
	Restart  func()                             // the box starts again (the BOOT button)
	Reset    func()                             // back to setup: forgets the hub and settings, and starts again

	mu      sync.Mutex
	armed   time.Time // a first tap on Stop, waiting for the second
	ending  time.Time // a first tap on End, waiting for the second
	note    string    // a word about the last tap, for a few seconds
	noteAt  time.Time
	noteOK  bool // good news, not a problem
	view    view
	scroll  int // how far the recordings list is scrolled
	touch   *touch
	chosen  recorder.Session // the recording whose QR code is showing
	link    string           // the QR code showing: a recording's, or setup's
	touched time.Time
	asking  question  // the BOOT button's question, in the ask view
	pressed time.Time // the BOOT button, down since
}

type view int

const (
	home   view = iota
	list        // the recordings on the card
	detail      // one recording's QR code
	setup       // the setup page's QR code
	ask         // the BOOT button's question: restart, or back to setup
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
	case setup:
		if inBack(x, y) {
			s.show(home)
		}
	case ask:
		s.tapAsk(x, y)
	}
}

func near(x, y, cx, cy int) bool {
	dx, dy := x-cx, y-cy
	return dx*dx+dy*dy <= (buttonR+tapMargin)*(buttonR+tapMargin)
}

func (s *Screen) say(note string) {
	s.mu.Lock()
	s.note, s.noteAt, s.noteOK = note, time.Now(), false
	s.mu.Unlock()
}

// Saved: the setup page saved something. If its QR code is showing, the job's done: back home.
func (s *Screen) Saved() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.view == setup {
		s.view, s.link = home, ""
		s.note, s.noteAt, s.noteOK = "Saved on your phone", time.Now(), true
	}
}

func (s *Screen) openSetup(note string) {
	link := s.Setup()
	s.mu.Lock()
	s.view, s.link = setup, link
	s.mu.Unlock()
	if note != "" {
		s.say(note)
	}
}

func (s *Screen) tapHome(x, y int) {
	st := s.State()
	switch {
	case y < 40 && s.Setup != nil && !st.Paired: // the top bar: setup, with no hub (with one, the hub has it)
		s.openSetup("")
	case near(x, y, buttonX, buttonY):
		s.tapRecord(st)
	case near(x, y, liveX, buttonY):
		s.tapLive(st)
	case x >= pillLeft-4 && x <= pillEnd+4 && y >= buttonY-buttonR-tapMargin && y <= buttonY+buttonR+tapMargin &&
		s.Sessions != nil:
		s.mu.Lock()
		s.view, s.scroll = list, 0
		s.mu.Unlock()
	}
}

// tapRecord: Record takes one tap, Stop two. While live it records anyway, so it can't stop.
func (s *Screen) tapRecord(st spoke.State) {
	switch {
	case st.Mode == "live":
		s.say("It records while it's live")
	case !st.Manual && !st.Signal.Present:
		s.say("No HDMI signal to record")
	default:
		s.mu.Lock()
		defer s.mu.Unlock()
		if st.Manual && time.Since(s.armed) > armFor {
			s.armed = time.Now() // the first tap on Stop only arms it
			return
		}
		s.armed = time.Time{}
		s.Record()
	}
}

// tapLive: Go live takes one tap; End takes two, so a stray touch can't end a broadcast.
func (s *Screen) tapLive(st spoke.State) {
	live := st.Mode == "live"
	switch {
	case st.Pending != "":
	case !live && !st.Signal.Present:
		s.say("No HDMI signal to go live with")
	case !live && st.Paired && !st.HubOK:
		s.say("The hub can't be reached")
	case !live:
		if err := s.Live(true); errors.Is(err, spoke.ErrNoKeys) {
			s.openSetup("Add a stream key to go live")
		}
	default:
		s.mu.Lock()
		first := time.Since(s.ending) > armFor
		if first {
			s.ending = time.Now()
		} else {
			s.ending = time.Time{}
		}
		s.mu.Unlock()
		if !first {
			_ = s.Live(false)
		}
	}
}

// canGoLive: Go live needs a picture, and a hub that answers (or no hub at all).
func canGoLive(st spoke.State) bool {
	return st.Mode == "live" || (st.Pending == "" && st.Signal.Present && (!st.Paired || st.HubOK))
}

// ring draws a round button's white ring (red while a second tap is awaited) around ink.
func ring(img *image.RGBA, cx int, armed bool) {
	c := ash
	if armed {
		c = rec
	}
	fill(img, c, circle(float32(cx), buttonY, buttonR))
	fill(img, ink, circle(float32(cx), buttonY, buttonR-ringW))
}

// broadcastIcon is Go live: a dot sending out two arcs of signal each way.
func broadcastIcon(img *image.RGBA, cx int, c color.Color) {
	x, y := float32(cx), float32(buttonY)
	fill(img, c, circle(x, y, 3.5))
	for _, r := range []float32{8.5, 14} {
		fill(img, c, arc(x, y, r, 2.2, -0.7, 0.7))                // right
		fill(img, c, arc(x, y, r, 2.2, math.Pi-0.7, math.Pi+0.7)) // left
	}
}

func (s *Screen) drawButtons(img *image.RGBA, st spoke.State, now time.Time) {
	s.mu.Lock()
	armed := !s.armed.IsZero() && now.Sub(s.armed) <= armFor
	ending := now.Sub(s.ending) <= armFor && st.Mode == "live"
	s.mu.Unlock()
	// Recordings
	fill(img, ash, roundRect(pillLeft, buttonY-buttonR, pillEnd, buttonY+buttonR, buttonR))
	fill(img, ink, roundRect(pillLeft+ringW, buttonY-buttonR+ringW, pillEnd-ringW, buttonY+buttonR-ringW, buttonR-ringW))
	text(img, button, "Recordings", (pillLeft+pillEnd)/2, buttonY+5, 0, ash)
	// Go live / End
	ring(img, liveX, ending)
	icon := ember
	switch {
	case st.Mode == "live":
		icon = rec
	case !canGoLive(st):
		icon = faint
	}
	broadcastIcon(img, liveX, icon)
	// Record / Stop: locked on while live
	ring(img, buttonX, armed)
	inside := rec
	if !st.Recording && !st.Signal.Present {
		inside = faint
	}
	if st.Recording {
		fill(img, inside, roundRect(buttonX-9, buttonY-9, buttonX+9, buttonY+9, 3))
	} else {
		fill(img, inside, circle(buttonX, buttonY, buttonR-7))
	}
}

// Link is the phone page the QR code on screen points at, if one is showing.
func (s *Screen) Link() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.view != detail && s.view != setup {
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

// drawing: the fonts are shared and can't draw two things at once (the emulator's page, a phone
// and the panel can all ask for the screen together), so one render draws at a time.
var drawing sync.Mutex

func (s *Screen) Render(now time.Time) *image.RGBA {
	drawing.Lock()
	defer drawing.Unlock()
	s.mu.Lock()
	if s.view != home && now.Sub(s.touched) > idleFor && s.pressed.IsZero() {
		s.view, s.scroll = home, 0
	}
	held := s.held(now)
	v, q := s.view, s.asking
	s.mu.Unlock()
	img := image.NewRGBA(image.Rect(0, 0, Size, Size))
	draw.Draw(img, img.Bounds(), image.NewUniform(ink), image.Point{}, draw.Src)
	if held >= holdShown && held < holdFor { // the BOOT button held: counting down to setup
		v = ask
	}
	if st := s.State(); st.Updating != "" { // nothing to tap while it's updating: it starts again in a moment
		splashOn(img, "Updating to "+st.Updating+"…")
		return img
	}
	switch v {
	case ask:
		s.renderAsk(img, s.State(), q, held)
	case list:
		s.renderList(img, now)
	case detail:
		s.renderDetail(img)
	case setup:
		s.renderSetup(img)
	default:
		s.renderHome(img, now)
	}
	return img
}

// Splash is what the box's screen shows while it starts, before the spoke's up: the mark, its name,
// and a line (Starting…, or how installing it's going).
func Splash(line string) *image.RGBA {
	drawing.Lock()
	defer drawing.Unlock()
	img := image.NewRGBA(image.Rect(0, 0, Size, Size))
	splashOn(img, line)
	return img
}

func splashOn(img *image.RGBA, line string) {
	draw.Draw(img, img.Bounds(), image.NewUniform(ink), image.Point{}, draw.Src)
	draw.Draw(img, image.Rect(Size/2-32, 66, Size/2+32, 130), mark(64), image.Point{}, draw.Over)
	text(img, title, "TouchStream", Size/2, 160, 0, ash)
	text(img, small, line, Size/2, 184, 0, smoke)
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
	if !st.Paired && st.Mode != "live" {
		setupHint(img, now)
	}
	fill(img, dot, circle(225, 20, 5))

	// What it's doing
	line1, line2 := signalLine(st), streamLine(st)
	switch {
	case !st.Paired && st.Keys == 0 && st.Mode != "live":
		headline(img, "SET UP", ash, false)
		line2 = "Tap the top for setup"
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
	s.mu.Lock()
	note, noteOK := s.note, s.noteOK
	if now.Sub(s.noteAt) > noteFor {
		note = ""
	}
	ending := now.Sub(s.ending) <= armFor && st.Mode == "live"
	s.mu.Unlock()
	if !st.Paired && st.Keys > 0 && st.Mode != "live" {
		line2 = "No hub · Go live streams straight to YouTube"
	}
	line2Color := smoke
	switch {
	case note != "" && noteOK:
		line2, line2Color = note, ok
	case note != "" || st.Problem != "":
		line2, line2Color = nonEmpty(note, st.Problem), bad
	case st.Pending == "live":
		line2 = "Asking the hub to go live…"
	case st.Pending == "off":
		line2 = "Ending the broadcast…"
	case ending:
		line2 = "Tap End again to finish"
	case armed:
		line2 = "Tap again to stop recording"
	case waiting(line2):
		if strings.Contains(line2, "retrying") {
			line2Color = bad
		}
	case st.Mode == "live" && st.Direct:
		line2, line2Color = "Live "+clock(now.Sub(st.LiveSince))+" · straight to YouTube", rec
	case st.Mode == "live":
		line2, line2Color = "Live "+clock(now.Sub(st.LiveSince))+" · "+strings.ToLower(line2[:1])+line2[1:], rec
	case st.Recording:
		line2, line2Color = "Recording "+clock(now.Sub(st.RecordedAt)), rec
	}
	text(img, mono, fit(mono, line1, 220), Size/2, 136, 0, signalColor(st))
	marquee(img, small, line2, 157, line2Color, now)

	s.drawButtons(img, st, now)
}

// marquee draws a line centered or, too long to fit, scrolling through it: a pause at the start,
// along at 30 px a second to its end, a pause there, and back to the start.
func marquee(img *image.RGBA, f font.Face, line string, y int, c color.Color, now time.Time) {
	const width, left, pause, speed = 224, (Size - 224) / 2, 1.5, 30.0
	w := textWidth(f, line)
	if w <= width {
		text(img, f, line, Size/2, y, 0, c)
		return
	}
	travel := float64(w - width)
	t := math.Mod(float64(now.UnixMilli())/1000, 2*pause+travel/speed)
	off := min(max(t-pause, 0)*speed, travel)
	area := img.SubImage(image.Rect(left, y-14, left+width, y+5)).(*image.RGBA)
	text(area, f, line, left-int(off), y, -1, c)
}

// setupHint: with no hub, the gray dot opens into "Tap for setup" for a moment every 8 seconds.
func setupHint(img *image.RGBA, now time.Time) {
	const every, shown, ease, hint = 8.0, 2.8, 0.3, "Tap for setup"
	t := math.Mod(float64(now.UnixMilli())/1000, every)
	if t >= shown {
		return
	}
	open := min(1, t/ease, (shown-t)/ease)
	w := float32(open) * float32(textWidth(small, hint)+30)
	if w < 14 {
		return
	}
	fill(img, charcoal, roundRect(232-w, 9, 232, 31, 11))
	area := img.SubImage(image.Rect(int(232-w)+8, 9, 216, 31)).(*image.RGBA)
	text(area, small, hint, int(232-w)+11, 24, -1, ember)
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

// streamLine says what the spoke is doing with the hub or the stream, above all while it's waiting
// on one: connecting, or retrying after a failure.
func streamLine(st spoke.State) string {
	hub := map[string]string{"connecting": "Connecting to the hub…", "unreachable": "Can't reach the hub · retrying"}
	switch {
	case st.Direct && st.DirectNow == "connecting":
		return "Connecting to YouTube…"
	case st.Direct && st.DirectNow == "retrying":
		return "Can't reach YouTube · retrying"
	case st.Direct:
		return "Sending straight to YouTube"
	case st.Uploading != "":
		return "Uploading to hub · " + st.Uploading
	case st.Mode == "off" || st.Mode == "":
		if line := hub[st.HubNow]; line != "" && st.Paired {
			return line
		}
		return "Hub connected · " + st.IP
	}
	if st.Paired && st.HubNow == "unreachable" {
		return "Can't reach the hub · recording here" // running its own schedule
	}
	switch st.Uplink {
	case "sending":
		if st.Viewers != nil {
			return fmt.Sprintf("%s watching on YouTube", count(*st.Viewers))
		}
		return "Sending to hub"
	case "retrying":
		return "Can't reach the hub · retrying"
	}
	return "Connecting to the hub…"
}

// waiting: the stream line is about something not working yet, so it's shown instead of a timer.
func waiting(line string) bool {
	return strings.HasSuffix(line, "…") || strings.Contains(line, "retrying")
}

func clock(d time.Duration) string {
	s := max(int(d.Seconds()), 0)
	return fmt.Sprintf("%d:%02d:%02d", s/3600, s/60%60, s%60)
}

// count: 950, 1.2K, 34K: short enough for the screen.
func count(n int) string {
	switch {
	case n < 1000:
		return strconv.Itoa(n)
	case n < 10000:
		return strings.TrimSuffix(strconv.FormatFloat(float64(n)/1000, 'f', 1, 64), ".0") + "K"
	default:
		return strconv.Itoa(n/1000) + "K"
	}
}
