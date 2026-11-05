package share

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/Cinderwire-co/TouchStream/lite/internal/recorder"
)

func TestAnExpiredLinkSaysSo(t *testing.T) {
	s := &Shares{Recorder: &recorder.Recorder{Dir: t.TempDir()}, Dir: t.TempDir(), Name: func() string { return "Hall" }}
	mux := http.NewServeMux()
	s.Routes(mux)
	for _, path := range []string{"/r/nope/info", "/r/nope/video.mp4", "/download/nope", "/r/nope/clip/x"} {
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, httptest.NewRequest("GET", path, nil))
		if w.Code != http.StatusNotFound {
			t.Fatalf("%s: %d", path, w.Code)
		}
	}
}
