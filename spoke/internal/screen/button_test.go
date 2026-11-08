package screen

import (
	"image/png"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/spoke"
)

func buttonScreen(st spoke.State, did *[]string) *Screen {
	return &Screen{State: func() spoke.State { return st },
		Restart: func() { *did = append(*did, "restart") }, Reset: func() { *did = append(*did, "reset") }}
}

func waitFor(t *testing.T, did *[]string, n int) {
	for i := 0; i < 100 && len(*did) < n; i++ {
		time.Sleep(10 * time.Millisecond)
	}
}

func TestPressedItAsksToRestartAndWaitsForATap(t *testing.T) {
	var did []string
	s := buttonScreen(states["ready"], &did)
	s.Button(true)
	s.Button(false)
	if s.view != ask || s.asking != restart {
		t.Fatalf("view %v, asking %v", s.view, s.asking)
	}
	s.Tap(60, askY) // Cancel
	if s.view != home || len(did) != 0 {
		t.Fatalf("cancel: view %v, did %v", s.view, did)
	}
	s.Button(true)
	s.Button(false)
	s.Tap(180, askY)
	waitFor(t, &did, 1)
	if len(did) != 1 || did[0] != "restart" {
		t.Fatalf("did %v", did)
	}
}

func TestHeldItCountsDownThenAsksToGoBackToSetup(t *testing.T) {
	var did []string
	s := buttonScreen(states["ready"], &did)
	s.Button(true)
	s.pressed = time.Now().Add(-4 * time.Second)
	s.Render(time.Now()) // counting down: 6 seconds to go
	if s.view == ask {
		t.Fatal("asked before it was held long enough")
	}
	s.pressed = time.Now().Add(-holdFor - time.Second)
	s.Render(time.Now())
	if s.view != ask || s.asking != backToSetup {
		t.Fatalf("held: view %v, asking %v", s.view, s.asking)
	}
	s.Button(false) // let go after: still asking
	if s.view != ask || s.asking != backToSetup {
		t.Fatal("letting go dropped the question")
	}
	s.Tap(180, askY)
	waitFor(t, &did, 1)
	if len(did) != 1 || did[0] != "reset" {
		t.Fatalf("did %v", did)
	}
	// Let go part way: nothing
	s.Button(true)
	s.pressed = time.Now().Add(-5 * time.Second)
	s.Button(false)
	if s.view != home {
		t.Fatal("let go part way, and it asked")
	}
}

// With SCREEN_PREVIEWS set, the questions too.
func TestTheQuestionsDraw(t *testing.T) {
	dir := os.Getenv("SCREEN_PREVIEWS")
	var did []string
	for name, set := range map[string]func(*Screen){
		"ask-restart":  func(s *Screen) { s.view, s.asking = ask, restart },
		"ask-setup":    func(s *Screen) { s.view, s.asking = ask, backToSetup },
		"ask-counting": func(s *Screen) { s.pressed = time.Now().Add(-3 * time.Second) },
		"ask-live":     func(s *Screen) { s.view, s.asking = ask, restart },
	} {
		st := states["ready"]
		if name == "ask-live" {
			st = states["live"]
		}
		s := buttonScreen(st, &did)
		s.touched = time.Now()
		set(s)
		img := s.Render(time.Now())
		if dir != "" {
			f, _ := os.Create(filepath.Join(dir, name+".png"))
			_ = png.Encode(f, img)
			f.Close()
		}
	}
}
