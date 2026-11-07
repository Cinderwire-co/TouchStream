package screen

import (
	"bytes"
	"image"
	"image/color"
	"image/draw"
	"image/png"
	"math"

	xdraw "golang.org/x/image/draw"
	"golang.org/x/image/font"
	"golang.org/x/image/font/opentype"
	"golang.org/x/image/math/fixed"
	"golang.org/x/image/vector"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/assets"
)

// The TouchStream palette, as in the hub dashboard: status colors only for real status.
var (
	ink      = hex(0x0E0E0E)
	charcoal = hex(0x272727)
	ash      = hex(0xF2F2F3)
	smoke    = hex(0x8E8E93)
	faint    = hex(0x5E5E63)
	ember    = hex(0xFE5C09)
	ok       = hex(0x4ADE80)
	bad      = hex(0xF87171)
	rec      = hex(0xEF4444)
)

func hex(v uint32) color.RGBA { return color.RGBA{uint8(v >> 16), uint8(v >> 8), uint8(v), 0xFF} }

func face(file string, size float64) font.Face {
	data, err := assets.Read(file)
	if err != nil {
		panic(err)
	}
	f, err := opentype.Parse(data)
	if err != nil {
		panic(err)
	}
	ff, err := opentype.NewFace(f, &opentype.FaceOptions{Size: size, DPI: 72, Hinting: font.HintingFull})
	if err != nil {
		panic(err)
	}
	return &glyphs{Face: ff, seen: map[glyphKey]glyph{}}
}

// glyphs keeps each letter's shape once it's drawn: the box's one core draws the screen 20 times a
// second, and shaping every letter every time was a fifth of its work. (Drawing is one at a time.)
type glyphs struct {
	font.Face
	seen map[glyphKey]glyph
}

type glyphKey struct {
	r      rune
	fx, fy fixed.Int26_6 // where in its pixel it starts (with full hinting, always the start)
}

type glyph struct {
	dr      image.Rectangle // from the pixel it starts in
	mask    *image.Alpha    // a copy: the face reuses its own
	advance fixed.Int26_6
	ok      bool
}

func (g *glyphs) Glyph(dot fixed.Point26_6, r rune) (image.Rectangle, image.Image, image.Point, fixed.Int26_6, bool) {
	at := image.Pt(dot.X.Floor(), dot.Y.Floor())
	key := glyphKey{r, dot.X - fixed.I(at.X), dot.Y - fixed.I(at.Y)}
	gl, seen := g.seen[key]
	if !seen {
		dr, mask, mp, advance, ok := g.Face.Glyph(fixed.Point26_6{X: key.fx, Y: key.fy}, r)
		gl = glyph{advance: advance, ok: ok}
		if ok && !dr.Empty() {
			gl.dr, gl.mask = dr, image.NewAlpha(image.Rect(0, 0, dr.Dx(), dr.Dy()))
			draw.Draw(gl.mask, gl.mask.Bounds(), mask, mp, draw.Src)
		}
		g.seen[key] = gl
	}
	if gl.mask == nil {
		return image.Rectangle{}, nil, image.Point{}, gl.advance, gl.ok
	}
	return gl.dr.Add(at), gl.mask, image.Point{}, gl.advance, gl.ok
}

var (
	title  = face("Bricolage-SemiBold.ttf", 16)
	big    = face("Bricolage-Bold.ttf", 34)
	medium = face("Bricolage-Bold.ttf", 26)
	label  = face("Bricolage-SemiBold.ttf", 14)
	button = face("Bricolage-SemiBold.ttf", 13) // the buttons along the bottom
	mono   = face("JetBrainsMono-Medium.ttf", 13)
	small  = face("JetBrainsMono-Medium.ttf", 11)
	markPx = mark(22)
)

func mark(size int) image.Image {
	data, _ := assets.Read("mark.png")
	src, err := png.Decode(bytes.NewReader(data))
	if err != nil {
		panic(err)
	}
	dst := image.NewRGBA(image.Rect(0, 0, size, size))
	xdraw.CatmullRom.Scale(dst, dst.Bounds(), src, src.Bounds(), xdraw.Over, nil)
	return dst
}

func textWidth(f font.Face, s string) int { return font.MeasureString(f, s).Ceil() }

// text draws s with its baseline at y; align is -1 left of x, 0 centered on it, 1 right of it.
func text(dst draw.Image, f font.Face, s string, x, y, align int, c color.Color) {
	w := textWidth(f, s)
	switch align {
	case 0:
		x -= w / 2
	case 1:
		x -= w
	}
	d := font.Drawer{Dst: dst, Src: image.NewUniform(c), Face: f, Dot: fixed.P(x, y)}
	d.DrawString(s)
}

// fit shortens s with an ellipsis until it fits in width.
func fit(f font.Face, s string, width int) string {
	if textWidth(f, s) <= width {
		return s
	}
	r := []rune(s)
	for len(r) > 1 && textWidth(f, string(r)+"…") > width {
		r = r[:len(r)-1]
	}
	return string(r) + "…"
}

// pen draws a shape's outline: onto a rasterizer, or into a measure of how far it reaches.
type pen interface {
	MoveTo(x, y float32)
	LineTo(x, y float32)
	CubeTo(bx, by, cx, cy, dx, dy float32)
	ClosePath()
}

// Shapes are anti-aliased: the panel is small and close, so jagged edges show. Each is rasterized
// over only the box it reaches, not the whole screen: the box's one core draws it 20 times a second.
func fill(dst *image.RGBA, c color.Color, path func(z pen)) {
	var m reach
	path(&m)
	r := m.box().Intersect(dst.Bounds())
	if r.Empty() {
		return
	}
	z := vector.NewRasterizer(r.Dx(), r.Dy())
	path(moved{z, float32(r.Min.X), float32(r.Min.Y)})
	z.Draw(dst, r, image.NewUniform(c), image.Point{})
}

// reach is how far a shape's outline goes (a curve stays within its control points).
type reach struct {
	x0, y0, x1, y1 float32
	any            bool
}

func (m *reach) add(x, y float32) {
	if !m.any {
		m.x0, m.y0, m.x1, m.y1, m.any = x, y, x, y, true
		return
	}
	m.x0, m.y0, m.x1, m.y1 = min(m.x0, x), min(m.y0, y), max(m.x1, x), max(m.y1, y)
}

func (m *reach) MoveTo(x, y float32) { m.add(x, y) }
func (m *reach) LineTo(x, y float32) { m.add(x, y) }
func (m *reach) CubeTo(bx, by, cx, cy, dx, dy float32) {
	m.add(bx, by)
	m.add(cx, cy)
	m.add(dx, dy)
}
func (m *reach) ClosePath() {}

func (m *reach) box() image.Rectangle {
	if !m.any {
		return image.Rectangle{}
	}
	return image.Rect(int(math.Floor(float64(m.x0))), int(math.Floor(float64(m.y0))),
		int(math.Ceil(float64(m.x1))), int(math.Ceil(float64(m.y1))))
}

// moved draws onto a rasterizer whose (0, 0) is at (dx, dy) on the screen.
type moved struct {
	z      *vector.Rasterizer
	dx, dy float32
}

func (p moved) MoveTo(x, y float32) { p.z.MoveTo(x-p.dx, y-p.dy) }
func (p moved) LineTo(x, y float32) { p.z.LineTo(x-p.dx, y-p.dy) }
func (p moved) CubeTo(bx, by, cx, cy, dx, dy float32) {
	p.z.CubeTo(bx-p.dx, by-p.dy, cx-p.dx, cy-p.dy, dx-p.dx, dy-p.dy)
}
func (p moved) ClosePath() { p.z.ClosePath() }

const kappa = 0.5523 // cubic Bézier control distance for a quarter circle

func circle(cx, cy, r float32) func(z pen) {
	return func(z pen) {
		k := r * kappa
		z.MoveTo(cx+r, cy)
		z.CubeTo(cx+r, cy+k, cx+k, cy+r, cx, cy+r)
		z.CubeTo(cx-k, cy+r, cx-r, cy+k, cx-r, cy)
		z.CubeTo(cx-r, cy-k, cx-k, cy-r, cx, cy-r)
		z.CubeTo(cx+k, cy-r, cx+r, cy-k, cx+r, cy)
		z.ClosePath()
	}
}

func roundRect(x0, y0, x1, y1, r float32) func(z pen) {
	return func(z pen) {
		r = float32(math.Min(float64(r), float64(min(x1-x0, y1-y0)/2)))
		k := r * kappa
		z.MoveTo(x0+r, y0)
		z.LineTo(x1-r, y0)
		z.CubeTo(x1-r+k, y0, x1, y0+r-k, x1, y0+r)
		z.LineTo(x1, y1-r)
		z.CubeTo(x1, y1-r+k, x1-r+k, y1, x1-r, y1)
		z.LineTo(x0+r, y1)
		z.CubeTo(x0+r-k, y1, x0, y1-r+k, x0, y1-r)
		z.LineTo(x0, y0+r)
		z.CubeTo(x0, y0+r-k, x0+r-k, y0, x0+r, y0)
		z.ClosePath()
	}
}

// arc is a band of width w along a circle of radius r, from angle a0 to a1 (radians, clockwise
// from 3 o'clock), with round ends.
func arc(cx, cy, r, w float32, a0, a1 float64) func(z pen) {
	return func(z pen) {
		const steps = 16
		at := func(rad float32, a float64) (float32, float32) {
			return cx + rad*float32(math.Cos(a)), cy + rad*float32(math.Sin(a))
		}
		z.MoveTo(at(r+w/2, a0))
		for i := 1; i <= steps; i++ {
			z.LineTo(at(r+w/2, a0+(a1-a0)*float64(i)/steps))
		}
		for i := steps; i >= 0; i-- {
			z.LineTo(at(r-w/2, a0+(a1-a0)*float64(i)/steps))
		}
		z.ClosePath()
	}
}
