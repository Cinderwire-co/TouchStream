package screen

import (
	"image/png"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/Cinderwire-co/TouchStream/lite/internal/media"
	"github.com/Cinderwire-co/TouchStream/lite/internal/spoke"
)

var hdmi = media.Signal{Present: true, Width: 1920, Height: 1080, FPS: 30}

// Every state the screen can be in. With SCREEN_PREVIEWS=dir set, they're written there as PNGs.
var states = map[string]spoke.State{
	"setup":     {Name: "picokvm", IP: "192.168.1.150", Signal: hdmi},
	"ready":     {Name: "Auditorium", Paired: true, HubOK: true, Mode: "off", Signal: hdmi, IP: "192.168.1.150"},
	"nosignal":  {Name: "Auditorium", Paired: true, HubOK: true, Mode: "off", IP: "192.168.1.150"},
	"preview":   {Name: "Auditorium", Paired: true, HubOK: true, Mode: "preview", Signal: hdmi, Uplink: "sending"},
	"live":      {Name: "Auditorium", Paired: true, HubOK: true, Mode: "live", Signal: hdmi, Uplink: "sending", Recording: true},
	"recording": {Name: "South Campus Chapel", Paired: true, HubOK: false, Mode: "off", Signal: hdmi, Recording: true, Manual: true, IP: "192.168.1.150"},
	"uploading": {Name: "Auditorium", Paired: true, HubOK: true, Mode: "off", Signal: hdmi, Uploading: "2 of 5"},
}

func TestEveryStateDraws(t *testing.T) {
	dir := os.Getenv("SCREEN_PREVIEWS")
	for name, st := range states {
		st.RecordedAt = time.Now().Add(-83 * time.Second)
		s := &Screen{State: func() spoke.State { return st }, Record: func() bool { return true }}
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
