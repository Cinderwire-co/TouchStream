package setup

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/config"
)

func TestStreamKeysAreKeptButNeverShownAgain(t *testing.T) {
	store, _ := config.Open(t.TempDir())
	told := 0
	s := &Setup{Store: store, Rates: []string{"30", "60"}, Saved: func() { told++ }}
	mux := http.NewServeMux()
	s.Routes(mux)
	link := "/setup/" + s.Link()
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, httptest.NewRequest("POST", link+"/destinations", strings.NewReader(`{"key":"abcd-efgh-ijkl"}`)))
	if w.Code != 200 || strings.Contains(w.Body.String(), "abcd-efgh") || !strings.Contains(w.Body.String(), "••••ijkl") {
		t.Fatalf("%d %s", w.Code, w.Body)
	}
	if d := store.Get().Destinations; len(d) != 1 || d[0].Key != "abcd-efgh-ijkl" || d[0].URL != YouTube {
		t.Fatalf("kept: %+v", d)
	}
	if told != 1 {
		t.Fatal("the screen wasn't told it was saved")
	}
}

func TestNothingChangesWhileOnAHub(t *testing.T) {
	store, _ := config.Open(t.TempDir())
	_ = store.Update(func(c *config.Config) { c.HubURL, c.SpokeID, c.HubToken = "http://hub", "id", "t" })
	s := &Setup{Store: store, Rates: []string{"30"}}
	mux := http.NewServeMux()
	s.Routes(mux)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, httptest.NewRequest("POST", "/setup/"+s.Link()+"/destinations", strings.NewReader(`{"key":"k"}`)))
	if w.Code != http.StatusForbidden || len(store.Get().Destinations) != 0 {
		t.Fatalf("%d, %d keys", w.Code, len(store.Get().Destinations))
	}
}

func TestAnOldLinkStopsWorking(t *testing.T) {
	store, _ := config.Open(t.TempDir())
	s := &Setup{Store: store}
	mux := http.NewServeMux()
	s.Routes(mux)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, httptest.NewRequest("GET", "/setup/guess/state", nil))
	if w.Code != http.StatusNotFound {
		t.Fatalf("a made-up link got %d", w.Code)
	}
}
