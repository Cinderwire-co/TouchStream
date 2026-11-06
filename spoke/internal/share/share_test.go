package share

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/recorder"
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

func TestALinkOutlivesARestart(t *testing.T) {
	rec := &recorder.Recorder{Dir: t.TempDir()}
	session := filepath.Join(rec.Dir, "20261105-164017")
	_ = os.MkdirAll(session, 0o755)
	_ = os.WriteFile(filepath.Join(session, "part-00000.ts"), make([]byte, 188), 0o644)
	dir := t.TempDir()
	before := &Shares{Recorder: rec, Dir: dir, Name: func() string { return "Hall" }}
	link := before.Link("20261105-164017")
	// Its phone copy got made (ffmpeg isn't needed for this)
	_ = os.WriteFile(filepath.Join(dir, "20261105-164017.mp4"), []byte("mp4"), 0o644)
	before.mu.Lock()
	before.prepared["20261105-164017"].state = "ready"
	before.saveLocked()
	before.mu.Unlock()

	after := &Shares{Recorder: rec, Dir: dir, Name: func() string { return "Hall" }} // the box restarted
	mux := http.NewServeMux()
	after.Routes(mux)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, httptest.NewRequest("GET", "/r/"+link+"/info", nil))
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"state":"ready"`) {
		t.Fatalf("after a restart: %d %s", w.Code, w.Body)
	}
}

func TestThePageAlwaysGetsJSON(t *testing.T) {
	s := &Shares{Recorder: &recorder.Recorder{Dir: t.TempDir()}, Dir: t.TempDir(), Name: func() string { return "" }}
	mux := http.NewServeMux()
	s.Routes(mux)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, httptest.NewRequest("POST", "/r/gone/clip", strings.NewReader(`{"start":1,"end":5}`)))
	if w.Code != http.StatusNotFound || w.Header().Get("Content-Type") != "application/json" {
		t.Fatalf("%d %q", w.Code, w.Header().Get("Content-Type"))
	}
}
