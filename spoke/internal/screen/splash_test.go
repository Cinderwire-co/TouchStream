package screen

import (
	"image/png"
	"os"
	"path/filepath"
	"testing"
)

// With SCREEN_PREVIEWS set, the splash too.
func TestSplashDraws(t *testing.T) {
	img := Splash("Starting…")
	if img.Bounds().Dx() != Size {
		t.Fatal(img.Bounds())
	}
	if dir := os.Getenv("SCREEN_PREVIEWS"); dir != "" {
		f, _ := os.Create(filepath.Join(dir, "splash.png"))
		_ = png.Encode(f, img)
		f.Close()
	}
}
