package screen

import (
	"image/png"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/media"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/spoke"
)

var hdmi = media.Signal{Present: true, Width: 1920, Height: 1080, FPS: 30}

// Every state the screen can be in. With SCREEN_PREVIEWS=dir set, they're written there as PNGs.
var states = map[string]spoke.State{
	"setup":     {Name: "picokvm", IP: "192.168.1.150", Signal: hdmi},
	"ready":     {Name: "Auditorium", Paired: true, HubOK: true, Mode: "off", Signal: hdmi, IP: "192.168.1.150"},
	"nosignal":  {Name: "Auditorium", Paired: true, HubOK: true, Mode: "off", IP: "192.168.1.150"},
	"preview":   {Name: "Auditorium", Paired: true, HubOK: true, Mode: "preview", Signal: hdmi, Uplink: "sending"},
	"live":      {Name: "Auditorium", Paired: true, HubOK: true, Mode: "live", Signal: hdmi, Uplink: "sending", Recording: true},
	"starting":  {Name: "Auditorium", Paired: true, HubOK: true, Mode: "off", Signal: hdmi, Pending: "live"},
	"offline":   {Name: "Auditorium", Paired: true, HubOK: false, Mode: "off", Signal: hdmi, Problem: "Can't reach the hub"},
	"recording": {Name: "South Campus Chapel", Paired: true, HubOK: false, Mode: "off", Signal: hdmi, Recording: true, Manual: true, IP: "192.168.1.150"},
	"uploading": {Name: "Auditorium", Paired: true, HubOK: true, Mode: "off", Signal: hdmi, Uploading: "2 of 5"},
}

func TestEveryStateDraws(t *testing.T) {
	dir := os.Getenv("SCREEN_PREVIEWS")
	for name, st := range states {
		st.RecordedAt, st.LiveSince = time.Now().Add(-83*time.Second), time.Now().Add(-754*time.Second)
		s := &Screen{State: func() spoke.State { return st }, Record: func() bool { return true }, Live: func(bool) error { return nil },
			Setup: func() string { return "http://192.168.1.150:6077/setup/abc" }}
		if name == "recording" {
			s.Tap(buttonX, buttonY) // the first tap on Stop arms it
		}
		img := s.Render(time.Now())
		if img.Bounds().Dx() != Size {
			t.Fatalf("%s: %v", name, img.Bounds())
		}
		if dir != "" {
			f, _ := os.Create(filepath.Join(dir, name+".png"))
			_ = png.Encode(f, img)
			f.Close()
		}
	}
}

func TestStopTakesTwoTaps(t *testing.T) {
	st := states["recording"]
	toggles := 0
	s := &Screen{State: func() spoke.State { return st }, Record: func() bool { toggles++; return false }}
	s.Tap(buttonX, buttonY)
	if toggles != 0 {
		t.Fatal("one tap stopped the recording")
	}
	s.Tap(buttonX+5, buttonY-5)
	if toggles != 1 {
		t.Fatal("the second tap didn't stop it")
	}
}

func TestTheButtonsDontOverlap(t *testing.T) {
	if w := textWidth(button, "Recordings"); w > pillEnd-pillLeft-2*(ringW+6) {
		t.Errorf("Recordings is %d px wide, too wide for its button", w)
	}
	if pillEnd+3 > liveX-buttonR || liveX+buttonR+3 > buttonX-buttonR || buttonX+buttonR > Size-4 {
		t.Error("the buttons along the bottom overlap or run off the screen")
	}
}

func TestGoLiveTakesOneTapAndEndTakesTwo(t *testing.T) {
	st := states["ready"]
	var asked []bool
	s := &Screen{State: func() spoke.State { return st }, Live: func(on bool) error { asked = append(asked, on); return nil }}
	s.Tap(liveX, buttonY)
	if len(asked) != 1 || !asked[0] {
		t.Fatalf("Go live: %v", asked)
	}
	st = states["live"]
	s.Tap(liveX, buttonY)
	if len(asked) != 1 {
		t.Fatal("one tap ended the broadcast")
	}
	s.Tap(liveX, buttonY)
	if len(asked) != 2 || asked[1] {
		t.Fatalf("End: %v", asked)
	}
}

func TestNoSignalNoGoingLive(t *testing.T) {
	st := states["nosignal"]
	called := false
	s := &Screen{State: func() spoke.State { return st }, Live: func(bool) error { called = true; return nil }}
	s.Tap(liveX, buttonY)
	if called {
		t.Fatal("went live with no HDMI signal")
	}
}

func TestGoLiveWithNoHubOrKeysOpensSetup(t *testing.T) {
	st := states["setup"]
	s := &Screen{State: func() spoke.State { return st }, Live: func(bool) error { return spoke.ErrNoKeys },
		Setup: func() string { return "http://box/setup/t" }}
	s.Tap(liveX, buttonY)
	if s.Link() != "http://box/setup/t" {
		t.Fatalf("setup didn't open: %q", s.Link())
	}
}

func TestSavingOnThePhoneGoesBackHome(t *testing.T) {
	st := states["setup"]
	s := &Screen{State: func() spoke.State { return st }, Setup: func() string { return "http://box/setup/t" }}
	s.Tap(120, 20) // the top: setup
	if s.Link() == "" {
		t.Fatal("setup didn't open")
	}
	s.Saved()
	if s.Link() != "" || s.view != home {
		t.Fatal("still on setup after saving")
	}
}

func TestRendersAtOnce(t *testing.T) {
	st := states["live"]
	s := &Screen{State: func() spoke.State { return st }}
	var wg sync.WaitGroup
	for range 8 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for range 20 {
				s.Render(time.Now())
			}
		}()
	}
	wg.Wait()
}
