package screen

import (
	"image"
	"image/color"
	"strconv"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/spoke"
)

// The box's BOOT button: pressed, the screen asks whether to restart; held for holdFor, whether to
// go back to setup (forgetting the hub and its settings: its recordings stay). Either waits for a
// tap on the screen. (The RESET button restarts the box itself, at once: nothing can ask first.)

type question int

const (
	restart question = iota
	backToSetup
)

const (
	holdFor   = 10 * time.Second
	holdShown = 1500 * time.Millisecond // held this long, the screen counts down to setup
	askY      = 190                     // the question's buttons: Cancel, and what it's for
	askR      = 22
)

// Button is the BOOT button going down, or coming up.
func (s *Screen) Button(down bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now()
	s.touched = now
	if down {
		s.pressed = now
		return
	}
	if s.pressed.IsZero() {
		return
	}
	held := now.Sub(s.pressed)
	s.pressed = time.Time{}
	switch {
	case held >= holdFor:
		s.view, s.asking = ask, backToSetup
	case held < holdShown:
		s.view, s.asking = ask, restart
	default: // let go before setup: nothing
		if s.view == ask && s.asking == backToSetup {
			s.view = home
		}
	}
}

// held is how long the BOOT button's been down (0: it's up); held for holdFor, setup's asked about.
func (s *Screen) held(now time.Time) time.Duration {
	if s.pressed.IsZero() {
		return 0
	}
	h := now.Sub(s.pressed)
	if h >= holdFor {
		s.view, s.asking = ask, backToSetup
	}
	return h
}

func (s *Screen) tapAsk(x, y int) {
	s.mu.Lock()
	q := s.asking
	s.mu.Unlock()
	if y < askY-askR-tapMargin || y > askY+askR+tapMargin {
		return
	}
	switch {
	case x < Size/2: // Cancel
		s.show(home)
	case q == restart && s.Restart != nil:
		s.show(home)
		s.say("Restarting…")
		go s.Restart()
	case q == backToSetup && s.Reset != nil:
		s.show(home)
		s.say("Back to setup: restarting…")
		go s.Reset()
	}
}

func (s *Screen) renderAsk(img *image.RGBA, st spoke.State, q question, held time.Duration) {
	title, lines, action := "Restart?", []string{"The box starts again,", "in about a minute."}, "Restart"
	if q == backToSetup {
		title, lines, action = "Back to setup?", []string{"It forgets its hub and settings.", "Recordings stay on the card."}, "Reset"
	}
	if st.Mode == "live" {
		lines = append(lines, "This ends the live broadcast.")
	}
	counting := held >= holdShown && held < holdFor // still holding: counting down
	if counting {
		title = "Back to setup?"
		lines = []string{"Keep holding the button:", "in " + strconv.Itoa(int((holdFor-held+time.Second-1)/time.Second)) + " seconds"}
	}
	text(img, medium, title, Size/2, 64, 0, ash)
	for i, line := range lines {
		c := smoke
		if line == "This ends the live broadcast." {
			c = rec
		}
		text(img, small, line, Size/2, 100+i*20, 0, c)
	}
	if counting {
		return
	}
	pill(img, 12, Size/2-6, "Cancel", ash)
	pill(img, Size/2+6, Size-12, action, rec)
}

// pill is one of the question's buttons: a ring with its word.
func pill(img *image.RGBA, x0, x1 int, word string, c color.Color) {
	fill(img, c, roundRect(float32(x0), askY-askR, float32(x1), askY+askR, askR))
	fill(img, ink, roundRect(float32(x0)+ringW, askY-askR+ringW, float32(x1)-ringW, askY+askR-ringW, askR-ringW))
	text(img, button, word, (x0+x1)/2, askY+5, 0, c)
}
