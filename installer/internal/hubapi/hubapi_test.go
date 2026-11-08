package hubapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// A hub that wants a session cookie (as the real one does) before it registers a spoke.
func fakeHub(t *testing.T) *httptest.Server {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/login", func(w http.ResponseWriter, r *http.Request) {
		var body struct{ Password string }
		json.NewDecoder(r.Body).Decode(&body)
		if body.Password != "right-password" {
			w.WriteHeader(401)
			w.Write([]byte(`{"detail":"Wrong password"}`))
			return
		}
		http.SetCookie(w, &http.Cookie{Name: "touchstream_session", Value: "s"})
	})
	mux.HandleFunc("POST /api/spokes", func(w http.ResponseWriter, r *http.Request) {
		if c, err := r.Cookie("touchstream_session"); err != nil || c.Value != "s" {
			w.WriteHeader(401)
			w.Write([]byte(`{"detail":"Sign in required"}`))
			return
		}
		w.Write([]byte(`{"spoke_id":"ab12cd34","token":"t","hub_url":"http://192.168.1.20:8080"}`))
	})
	return httptest.NewServer(mux)
}

func TestSigningInThenRegisteringASpoke(t *testing.T) {
	hub := fakeHub(t)
	defer hub.Close()
	c := New(hub.URL)
	ctx := context.Background()
	if _, err := c.MakeSpoke(ctx, "Auditorium"); err == nil {
		t.Fatal("registered a spoke without signing in")
	}
	if err := c.SignIn(ctx, "wrong"); err == nil || err.Error() != "Wrong password" {
		t.Fatalf("wrong password: %v", err)
	}
	if err := c.SignIn(ctx, "right-password"); err != nil {
		t.Fatal(err)
	}
	s, err := c.MakeSpoke(ctx, "Auditorium")
	if err != nil || s.ID != "ab12cd34" || s.HubURL != "http://192.168.1.20:8080" {
		t.Fatalf("%+v %v", s, err)
	}
}
