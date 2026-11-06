package screen

import (
	"image/png"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/spoke"
)

// With SCREEN_PREVIEWS set, frames of the scrolling live line and the setup hint, for a look.
func TestAnimationsDraw(t *testing.T) {
	dir := os.Getenv("SCREEN_PREVIEWS")
	live := spoke.State{Name: "Auditorium", Mode: "live", Direct: true, Signal: hdmi, Recording: true,
		LiveSince: time.UnixMilli(0).Add(-754 * time.Second)}
	alone := spoke.State{Name: "picokvm", Signal: hdmi}
	for name, st := range map[string]spoke.State{"marquee": live, "hint": alone} {
		s := &Screen{State: func() spoke.State { return st }}
		base := time.UnixMilli(0)
		for i, ms := range []int{0, 1000, 2600, 4200} {
			img := s.Render(base.Add(time.Duration(ms) * time.Millisecond))
			if dir != "" {
				f, _ := os.Create(filepath.Join(dir, name+"-"+string(rune('0'+i))+".png"))
				_ = png.Encode(f, img)
				f.Close()
			}
		}
	}
}
