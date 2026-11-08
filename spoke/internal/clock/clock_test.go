package clock

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestItFindsItsTimeZoneFromTheFirstLookupThatAnswers(t *testing.T) {
	down := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Error(w, "busy", 429) }))
	defer down.Close()
	junk := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("<html>")) }))
	defer junk.Close()
	ok := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("America/Denver\n")) }))
	defer ok.Close()
	old := Lookups
	defer func() { Lookups = old }()
	Lookups = []string{down.URL, junk.URL, ok.URL}
	if zone, err := Lookup(context.Background()); err != nil || zone != "America/Denver" {
		t.Fatalf("%q %v", zone, err)
	}
	Lookups = []string{down.URL, junk.URL}
	if _, err := Lookup(context.Background()); err == nil {
		t.Fatal("no error with nothing answering")
	}
}

func TestZones(t *testing.T) {
	for zone, want := range map[string]bool{"America/Chicago": true, "Europe/London": true, "UTC": true,
		"": false, "Local": false, "Mars/Olympus": false, "<html>": false} {
		if Valid(zone) != want {
			t.Errorf("%q: %v", zone, !want)
		}
	}
	old := time.Local
	defer func() { time.Local = old }()
	if err := Zone("Asia/Tokyo"); err != nil || time.Now().Location().String() != "Asia/Tokyo" {
		t.Fatalf("%v %v", err, time.Now().Location())
	}
}
