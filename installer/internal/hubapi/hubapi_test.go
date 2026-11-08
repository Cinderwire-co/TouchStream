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

// A spoke that came ready to go: the hub sees it on the network, adopts it, and it's named.
func TestAdoptingAndNamingASpokeOnTheNetwork(t *testing.T) {
	mux := http.NewServeMux()
	adopted, named := false, ""
	mux.HandleFunc("GET /api/dashboard", func(w http.ResponseWriter, r *http.Request) {
		if adopted {
			w.Write([]byte(`{"discovery":true,"discovered":[],"spokes":[{"id":"c8f7","name":"Spoke 4F2A","mode":"idle"}],"hub":{"ip":"192.168.1.20","port":8080}}`))
			return
		}
		w.Write([]byte(`{"discovery":true,"discovered":[{"ip":"192.168.1.207","name":"Spoke 4F2A","device_id":"unadopted"}],"spokes":[],"hub":{"ip":"192.168.1.20","port":8080}}`))
	})
	mux.HandleFunc("POST /api/discovered/192.168.1.207/adopt", func(w http.ResponseWriter, r *http.Request) {
		adopted = true
		w.Write([]byte(`{"id":"c8f7"}`))
	})
	mux.HandleFunc("PATCH /api/spokes/c8f7", func(w http.ResponseWriter, r *http.Request) {
		var body struct{ Name string }
		json.NewDecoder(r.Body).Decode(&body)
		named = body.Name
		w.Write([]byte(`{}`))
	})
	hub := httptest.NewServer(mux)
	defer hub.Close()
	c, ctx := New(hub.URL), context.Background()

	n, err := c.Nearby(ctx)
	if err != nil || !n.Listening || len(n.Found) != 1 || n.Found[0].IP != "192.168.1.207" || n.Found[0].Name != "Spoke 4F2A" || n.Hub.Port != 8080 {
		t.Fatalf("%+v %v", n, err)
	}
	id, err := c.Adopt(ctx, n.Found[0].IP)
	if err != nil || id != "c8f7" {
		t.Fatalf("adopt: %q %v", id, err)
	}
	if err := c.Rename(ctx, id, "Sanctuary"); err != nil || named != "Sanctuary" {
		t.Fatalf("rename: %q %v", named, err)
	}
	if n, _ = c.Nearby(ctx); len(n.Found) != 0 || len(n.Spokes) != 1 || n.Spokes[0].ID != "c8f7" {
		t.Fatalf("after: %+v", n)
	}
}
