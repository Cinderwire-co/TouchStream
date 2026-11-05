package screen

import (
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"sort"
	"strings"
	"time"

	"golang.org/x/image/font"
	"golang.org/x/image/vector"
	"rsc.io/qr"

	"github.com/Cinderwire-co/TouchStream/lite/internal/recorder"
)

const (
	headerH = 40 // the title bar, with Back on the left
	listTop = 44
	rowH    = 52
)

var white = color.RGBA{0xFF, 0xFF, 0xFF, 0xFF}

// sorted is the recordings, newest first.
func (s *Screen) sorted() []recorder.Session {
	if s.Sessions == nil {
		return nil
	}
	out := s.Sessions()
	sort.Slice(out, func(i, j int) bool { return out[i].Start.After(out[j].Start) })
	return out
}

func inBack(x, y int) bool { return x < 90 && y < headerH }

// scrollTo keeps the list between its top and its last row (call with s.mu held).
func (s *Screen) scrollTo(v int) {
	most := max(0, len(s.sorted())*rowH-(Size-listTop))
	s.scroll = min(max(v, 0), most)
}

func (s *Screen) tapList(x, y int) {
	if inBack(x, y) {
		s.show(home)
		return
	}
	if y < listTop {
		return
	}
	s.mu.Lock()
	i := (y - listTop + s.scroll) / rowH
	s.mu.Unlock()
	sessions := s.sorted()
	if i < 0 || i >= len(sessions) {
		return
	}
	link := s.Share(sessions[i].Name)
	s.mu.Lock()
	s.chosen, s.link, s.view = sessions[i], link, detail
	s.mu.Unlock()
}

func (s *Screen) show(v view) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.view = v
	if v != list {
		s.scroll = 0
	}
}

func header(img *image.RGBA, name, right string) {
	// Back: a chevron big enough to see, with a tap area far bigger (inBack)
	fill(img, ash, func(z *vector.Rasterizer) {
		z.MoveTo(20, 12)
		z.LineTo(22.5, 14.5)
		z.LineTo(17, 20)
		z.LineTo(22.5, 25.5)
		z.LineTo(20, 28)
		z.LineTo(12, 20)
		z.ClosePath()
	})
	room := Size - 34 - 14
	if right != "" {
		text(img, small, right, Size-14, 26, 1, smoke)
		room -= textWidth(small, right) + 10
	}
	text(img, title, fit(title, name, room), 34, 26, -1, ash)
	draw.Draw(img, image.Rect(0, headerH, Size, headerH+1), image.NewUniform(charcoal), image.Point{}, draw.Src)
}

func (s *Screen) renderList(img *image.RGBA, now time.Time) {
	sessions := s.sorted()
	header(img, "Recordings", fmt.Sprint(len(sessions)))
	if len(sessions) == 0 {
		text(img, label, "No recordings yet", Size/2, 128, 0, ash)
		text(img, small, "Tap Record on the home screen", Size/2, 150, 0, smoke)
		return
	}
	s.mu.Lock()
	scroll := s.scroll
	s.mu.Unlock()
	area := img.SubImage(image.Rect(0, listTop, Size, Size)).(*image.RGBA) // rows scroll under the title
	for i, r := range sessions {
		y := listTop + i*rowH - scroll
		if y+rowH < listTop || y > Size {
			continue
		}
		text(area, label, r.Start.Format("Mon Jan 2 · 3:04 PM"), 16, y+21, -1, ash)
		status, c := "", smoke
		switch {
		case r.Recording:
			status, c = "Recording now", rec
		case r.OnHub:
			status = length(r.Duration) + " · " + sizeLabel(r.Size) + " · on the hub"
		default:
			status = length(r.Duration) + " · " + sizeLabel(r.Size) + " · only here"
		}
		text(area, small, status, 16, y+39, -1, c)
		text(area, title, "›", Size-14, y+31, 1, faint)
		draw.Draw(area, image.Rect(16, y+rowH-1, Size-16, y+rowH), image.NewUniform(charcoal), image.Point{}, draw.Src)
	}
	if total := len(sessions) * rowH; total > Size-listTop {
		visible := Size - listTop
		h := max(24, visible*visible/total)
		top := listTop + (visible-h)*scroll/(total-visible)
		fill(img, faint, roundRect(Size-5, float32(top), Size-2, float32(top+h), 1.5))
	}
}

func (s *Screen) renderDetail(img *image.RGBA) {
	s.mu.Lock()
	r, link := s.chosen, s.link
	s.mu.Unlock()
	header(img, r.Start.Format("Mon Jan 2 · 3:04 PM"), length(r.Duration))
	state, problem := s.Prepared(r.Name)
	if state == "failed" {
		lines := wrap(small, nonEmpty(problem, "Couldn't open this recording"), 200)
		for i, line := range lines {
			text(img, small, line, Size/2, 120+i*18, 0, bad)
		}
		return
	}
	// Dark on white scans far more reliably than inverted, and every module lands on whole pixels
	code, err := qr.Encode(link, qr.L)
	if err != nil {
		return
	}
	const quiet = 2
	px := min(176/(code.Size+2*quiet), 6)
	side := px * (code.Size + 2*quiet)
	x0, y0 := (Size-side)/2, headerH+(Size-headerH-24-side)/2
	fill(img, white, roundRect(float32(x0), float32(y0), float32(x0+side), float32(y0+side), 6))
	for y := 0; y < code.Size; y++ {
		for x := 0; x < code.Size; x++ {
			if code.Black(x, y) {
				at := image.Pt(x0+(x+quiet)*px, y0+(y+quiet)*px)
				draw.Draw(img, image.Rectangle{at, at.Add(image.Pt(px, px))}, image.NewUniform(ink), image.Point{}, draw.Src)
			}
		}
	}
	caption := "Scan to watch, trim and save"
	if state == "preparing" {
		caption = "Getting it ready for phones…"
	}
	text(img, small, caption, Size/2, Size-12, 0, smoke)
}

func length(d time.Duration) string {
	sec := int(d.Round(time.Second).Seconds())
	if sec >= 3600 {
		return fmt.Sprintf("%d:%02d:%02d", sec/3600, sec/60%60, sec%60)
	}
	return fmt.Sprintf("%d:%02d", sec/60, sec%60)
}

func sizeLabel(n int64) string {
	if n >= 1<<30 {
		return fmt.Sprintf("%.1f GB", float64(n)/(1<<30))
	}
	return fmt.Sprintf("%d MB", (n+(1<<19))>>20)
}

func nonEmpty(v, fallback string) string {
	if v == "" {
		return fallback
	}
	return v
}

// wrap breaks s into lines that fit width.
func wrap(f font.Face, s string, width int) []string {
	var lines []string
	line := ""
	for _, word := range strings.Fields(s) {
		next := strings.TrimSpace(line + " " + word)
		if line != "" && textWidth(f, next) > width {
			lines, line = append(lines, line), word
		} else {
			line = next
		}
	}
	return append(lines, line)
}
