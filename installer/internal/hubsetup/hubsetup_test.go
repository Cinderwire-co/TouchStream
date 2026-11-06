package hubsetup

import (
	"bytes"
	"context"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"
)

// The scripts here are copies of the hub's own: they mustn't drift apart.
func TestTheScriptsMatchTheHubs(t *testing.T) {
	for _, name := range []string{"macos.sh", "windows.ps1"} {
		mine, err := scripts.ReadFile("scripts/" + name)
		if err != nil {
			t.Fatal(err)
		}
		theirs, err := os.ReadFile("../../../hub/install/" + name)
		if err != nil {
			t.Skip("not in the TouchStream repo")
		}
		if !bytes.Equal(mine, theirs) {
			t.Errorf("internal/hubsetup/scripts/%s differs from hub/install/%s: copy it over", name, name)
		}
	}
}

func TestFindingTheHubAmongOtherServers(t *testing.T) {
	other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.NotFound(w, r) }))
	defer other.Close()
	hub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"version":"0.1.0","setup_needed":true}`))
	}))
	defer hub.Close()
	port := func(s *httptest.Server) int { return s.Listener.Addr().(*net.TCPAddr).Port }
	free, _ := net.Listen("tcp", "127.0.0.1:0") // a port with nothing on it
	closed := free.Addr().(*net.TCPAddr).Port
	free.Close()
	start := time.Now()
	f := findOn(context.Background(), []int{closed, port(other), port(hub)})
	if !f.Running || f.URL != fmt.Sprintf("http://localhost:%d", port(hub)) || !f.SetupNeeded {
		t.Fatalf("found %+v", f)
	}
	if time.Since(start) > 3*time.Second {
		t.Fatalf("took %v", time.Since(start))
	}
	if f := findOn(context.Background(), []int{closed, port(other)}); f.Running {
		t.Fatalf("found a hub that isn't one: %+v", f)
	}
}
