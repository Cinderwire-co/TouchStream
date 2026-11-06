package panel

import (
	"bytes"
	"context"
	"encoding/binary"
	"fmt"
	"image"
	"image/color"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func picture() *image.RGBA {
	img := image.NewRGBA(image.Rect(0, 0, 240, 240))
	img.Set(0, 0, color.RGBA{255, 0, 0, 255})     // red, top left
	img.Set(239, 239, color.RGBA{0, 0, 255, 255}) // blue, bottom right
	img.Set(10, 0, color.RGBA{0, 255, 0, 255})    // green, near the top left
	return img
}

func pixel(out []byte, x, y int) uint16 { return binary.LittleEndian.Uint16(out[y*480+x*2:]) }

func TestThePictureGoesOnTurnedRound(t *testing.T) {
	out := make([]byte, 240*480)
	Pack(RGB565, picture(), false, out)
	if pixel(out, 0, 0) != 0xF800 || pixel(out, 239, 239) != 0x001F || pixel(out, 10, 0) != 0x07E0 {
		t.Fatalf("as it is: %04x %04x %04x", pixel(out, 0, 0), pixel(out, 239, 239), pixel(out, 10, 0))
	}
	Pack(RGB565, picture(), true, out) // the panel is upside down in the case
	if pixel(out, 239, 239) != 0xF800 || pixel(out, 0, 0) != 0x001F || pixel(out, 229, 239) != 0x07E0 {
		t.Fatalf("turned: %04x %04x %04x", pixel(out, 239, 239), pixel(out, 0, 0), pixel(out, 229, 239))
	}
}

// A file stands in for the framebuffer (it can't be asked its layout, so it's taken as RGB565)
func TestTheScreenIsDrawnOnTheFramebuffer(t *testing.T) {
	fb := filepath.Join(t.TempDir(), "fb0")
	if err := os.WriteFile(fb, make([]byte, 240*480), 0o600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	d := &Display{Path: fb, Rotate180: true, Render: func(time.Time) *image.RGBA { return picture() }}
	if err := d.Run(ctx); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(fb)
	want := make([]byte, 240*480)
	Pack(RGB565, picture(), true, want)
	if !bytes.Equal(got, want) {
		t.Fatal("the framebuffer doesn't have the screen")
	}
}

// event is one struct input_event, as this computer lays it out (the box's is 16 bytes)
func event(kind, code uint16, value int32) []byte {
	e := make([]byte, eventSize)
	binary.LittleEndian.PutUint16(e[2*longSize:], kind)
	binary.LittleEndian.PutUint16(e[2*longSize+2:], code)
	binary.LittleEndian.PutUint32(e[2*longSize+4:], uint32(value))
	return e
}

func joined(es ...[]byte) (b []byte) {
	for _, e := range es {
		b = append(b, e...)
	}
	return
}

type touches []string

func (ts *touches) touch() *Touch {
	note := func(kind string) func(int, int) {
		return func(x, y int) { *ts = append(*ts, fmt.Sprintf("%s %d,%d", kind, x, y)) }
	}
	return &Touch{Rotate180: true, Size: 240, Down: note("down"), Move: note("move"), Up: note("up")}
}

func TestATouchDownAMoveAndUp(t *testing.T) {
	var got touches
	tc, f, a := got.touch(), finger{}, axis{0, 239}
	tc.feed(&f, a, a, joined(event(evKey, btnTouch, 1), event(evAbs, absX, 10), event(evAbs, absY, 20), event(evSyn, 0, 0)))
	tc.feed(&f, a, a, joined(event(evAbs, absX, 30), event(evSyn, 0, 0)))
	tc.feed(&f, a, a, joined(event(evSyn, 0, 0))) // a report with nothing new
	tc.feed(&f, a, a, joined(event(evKey, btnTouch, 0), event(evSyn, 0, 0)))
	want := touches{"down 229,219", "move 209,219", "up 209,219"} // turned round: 239 - x, 239 - y
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("%v, want %v", got, want)
	}
}

func TestMultiTouchReportsAndTheDriversRange(t *testing.T) {
	var got touches
	tc, f := got.touch(), finger{}
	a := axis{0, 4095} // a driver that reports finer than the screen
	tc.Rotate180 = false
	tc.feed(&f, a, a, joined(event(evAbs, absMTTrackingID, 7), event(evAbs, absMTX, 4095), event(evAbs, absMTY, 0),
		event(evSyn, 0, 0), event(evAbs, absMTTrackingID, -1), event(evSyn, 0, 0)))
	if want := (touches{"down 239,0", "up 239,0"}); fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("%v, want %v", got, want)
	}
}
