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

	"github.com/Cinderwire-co/TouchStream/lite/internal/assets"
)

// The Pi spoke's palette (spoke/touchstream/ui/theme.py): status colors only for real status.
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
	return ff
}

var (
	title  = face("Bricolage-SemiBold.ttf", 16)
	big    = face("Bricolage-Bold.ttf", 34)
	medium = face("Bricolage-Bold.ttf", 26)
	label  = face("Bricolage-SemiBold.ttf", 14)
	mono   = face("JetBrainsMono-Medium.ttf", 13)
	small  = face("JetBrainsMono-Medium.ttf", 11)
	timer  = face("JetBrainsMono-Medium.ttf", 22)
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

// Shapes are anti-aliased: the panel is small and close, so jagged edges show.
func fill(dst *image.RGBA, c color.Color, path func(z *vector.Rasterizer)) {
	b := dst.Bounds()
	z := vector.NewRasterizer(b.Dx(), b.Dy())
	path(z)
	z.Draw(dst, b, image.NewUniform(c), image.Point{})
}

const kappa = 0.5523 // cubic Bézier control distance for a quarter circle

func circle(cx, cy, r float32) func(z *vector.Rasterizer) {
	return func(z *vector.Rasterizer) {
		k := r * kappa
		z.MoveTo(cx+r, cy)
		z.CubeTo(cx+r, cy+k, cx+k, cy+r, cx, cy+r)
		z.CubeTo(cx-k, cy+r, cx-r, cy+k, cx-r, cy)
		z.CubeTo(cx-r, cy-k, cx-k, cy-r, cx, cy-r)
		z.CubeTo(cx+k, cy-r, cx+r, cy-k, cx+r, cy)
		z.ClosePath()
	}
}

func roundRect(x0, y0, x1, y1, r float32) func(z *vector.Rasterizer) {
	return func(z *vector.Rasterizer) {
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
