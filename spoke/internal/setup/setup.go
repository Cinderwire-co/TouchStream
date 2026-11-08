// Package setup is the spoke's setup page, opened from a QR code on its screen, so a phone can
// do what the box's small screen can't: add stream keys for going live with no hub, connect it
// to a hub, and name it. Only a link from the screen opens it, so someone else on the network
// can't change the keys; a stream key is never shown again once saved, only its last 4 letters.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package setup

import (
	"cmp"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/assets"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/config"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/hub"
)

const (
	linkLife = 30 * time.Minute // how long the link works after it was last used
	expired  = "This link has expired. Open setup on the spoke's screen again."
	// YouTube's ingest for stream keys (YouTube Studio > Create > Go live > Stream)
	YouTube = "rtmps://a.rtmps.youtube.com/live2"
)

var ingest = regexp.MustCompile(`^rtmps?://[^\s/]+(/\S*)?$`)

// The stream's choices, as the hub offers them
var (
	resolutions = []string{"1080p", "720p"}
	qualities   = []map[string]any{
		{"kbps": 4000, "label": "Light", "note": "4 Mbps · slower connections"},
		{"kbps": 6000, "label": "Standard", "note": "6 Mbps · safe on most connections"},
		{"kbps": 8000, "label": "High", "note": "8 Mbps · great 1080p for YouTube"},
		{"kbps": 12000, "label": "Maximum", "note": "12 Mbps · needs fast upload"},
	}
)

type Setup struct {
	Store   *config.Store
	Version string
	Rates   []string // the frame rates the box can capture
	Saved   func()   // something was saved from the phone (the screen goes back home)

	mu      sync.Mutex
	token   string
	expires time.Time
}

// Link is the current setup link's token, made afresh if the last one expired.
func (s *Setup) Link() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.token == "" || time.Now().After(s.expires) {
		b := make([]byte, 12)
		_, _ = rand.Read(b)
		s.token = base64.RawURLEncoding.EncodeToString(b)
	}
	s.expires = time.Now().Add(linkLife)
	return s.token
}

func (s *Setup) valid(t string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.token == "" || t != s.token || time.Now().After(s.expires) {
		return false
	}
	s.expires = time.Now().Add(linkLife)
	return true
}

func (s *Setup) Routes(mux *http.ServeMux) {
	mux.HandleFunc("GET /setup/{token}", func(w http.ResponseWriter, r *http.Request) {
		page, _ := assets.Read("setup.html")
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		_, _ = w.Write(page)
	})
	mux.HandleFunc("GET /setup/{token}/state", s.with(func(w http.ResponseWriter, r *http.Request) {
		reply(w, http.StatusOK, s.state())
	}))
	mux.HandleFunc("POST /setup/{token}/destinations", s.with(func(w http.ResponseWriter, r *http.Request) {
		var d config.Destination
		if err := decode(w, r, &d); err != nil {
			return
		}
		d.Name, d.URL = strings.TrimSpace(d.Name), strings.TrimSpace(nonEmpty(d.URL, YouTube))
		d.Key = strings.TrimSpace(d.Key)
		switch {
		case !ingest.MatchString(d.URL):
			problem(w, "The address starts with rtmp:// or rtmps://")
			return
		case d.Key == "" || strings.ContainsAny(d.Key, " \t/"):
			problem(w, "Paste the stream key from YouTube Studio")
			return
		}
		d.Name = nonEmpty(d.Name, "YouTube")
		if err := s.Store.Update(func(c *config.Config) { c.Destinations = append(c.Destinations, d) }); err != nil {
			problem(w, err.Error())
			return
		}
		s.saved(w)
	}))
	mux.HandleFunc("DELETE /setup/{token}/destinations/{index}", s.with(func(w http.ResponseWriter, r *http.Request) {
		i, err := strconv.Atoi(r.PathValue("index"))
		var gone bool
		if err == nil {
			_ = s.Store.Update(func(c *config.Config) {
				if i >= 0 && i < len(c.Destinations) {
					c.Destinations, gone = append(c.Destinations[:i:i], c.Destinations[i+1:]...), true
				}
			})
		}
		if !gone {
			problem(w, "That key has already been removed")
			return
		}
		s.saved(w)
	}))
	mux.HandleFunc("POST /setup/{token}/hub", s.with(func(w http.ResponseWriter, r *http.Request) {
		var body struct{ Address, Code string }
		if err := decode(w, r, &body); err != nil {
			return
		}
		if strings.TrimSpace(body.Address) == "" || strings.TrimSpace(body.Code) == "" {
			problem(w, "Enter the hub's address and the code from Add spoke")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		defer cancel()
		if err := hub.Pair(ctx, s.Store, body.Address, strings.TrimSpace(body.Code), s.Version); err != nil {
			var hubErr *hub.Error
			if errors.As(err, &hubErr) {
				problem(w, "The hub said: "+hubErr.Detail)
			} else {
				problem(w, "Can't reach a hub at "+hub.NormalizeURL(body.Address))
			}
			return
		}
		s.saved(w)
	}))
	mux.HandleFunc("POST /setup/{token}/stream", s.with(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Resolution  string `json:"resolution"`
			Framerate   string `json:"framerate"`
			BitrateKbps int    `json:"bitrate_kbps"`
		}
		if err := decode(w, r, &body); err != nil {
			return
		}
		switch {
		case s.Store.Get().Paired():
			problem(w, "The hub sets this: change it in the spoke's settings in the dashboard")
			return
		case !slices.Contains(resolutions, body.Resolution) || !slices.Contains(s.Rates, body.Framerate):
			problem(w, "Pick a resolution and a frame rate")
			return
		case !slices.ContainsFunc(qualities, func(q map[string]any) bool { return q["kbps"] == body.BitrateKbps }):
			problem(w, "Pick a quality")
			return
		}
		_ = s.Store.Update(func(c *config.Config) {
			c.Resolution, c.Framerate, c.BitrateKbps = body.Resolution, body.Framerate, body.BitrateKbps
		})
		s.saved(w)
	}))
	mux.HandleFunc("POST /setup/{token}/name", s.with(func(w http.ResponseWriter, r *http.Request) {
		var body struct{ Name string }
		if err := decode(w, r, &body); err != nil {
			return
		}
		name := strings.TrimSpace(body.Name)
		if name == "" || len(name) > 60 {
			problem(w, "A name of up to 60 letters")
			return
		}
		_ = s.Store.Update(func(c *config.Config) { c.DeviceName = name })
		s.saved(w)
	}))
}

// state is what the page shows. Stream keys are never sent back: only their last 4 letters.
// saved answers a change made on the phone with the new state, and lets the screen know.
func (s *Setup) saved(w http.ResponseWriter) {
	if s.Saved != nil {
		s.Saved()
	}
	reply(w, http.StatusOK, s.state())
}

func (s *Setup) state() map[string]any {
	c := s.Store.Get()
	keys := make([]map[string]string, len(c.Destinations))
	for i, d := range c.Destinations {
		hint := d.Key
		if len(hint) > 4 {
			hint = hint[len(hint)-4:]
		}
		keys[i] = map[string]string{"name": d.Name, "url": d.URL, "key_hint": "••••" + hint}
	}
	return map[string]any{"name": c.DeviceName, "paired": c.Paired(), "hub": c.HubURL, "destinations": keys,
		"youtube": YouTube,
		"stream": map[string]any{"resolution": nonEmpty(c.Resolution, "1080p"), "framerate": nonEmpty(c.Framerate, "30"),
			"bitrate_kbps": cmp.Or(c.BitrateKbps, 8000)},
		"resolutions": resolutions, "rates": s.Rates, "qualities": qualities}
}

// with checks the link, and that there's no hub: with one, its settings are the hub's to change.
func (s *Setup) with(h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !s.valid(r.PathValue("token")) {
			reply(w, http.StatusNotFound, map[string]string{"error": expired})
			return
		}
		if r.Method != http.MethodGet && s.Store.Get().Paired() {
			reply(w, http.StatusForbidden, map[string]string{"error": "This spoke is connected to a hub: change it in the dashboard"})
			return
		}
		h(w, r)
	}
}

func decode(w http.ResponseWriter, r *http.Request, v any) error {
	err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16<<10)).Decode(v)
	if err != nil {
		problem(w, "That didn't come through: try again")
	}
	return err
}

func problem(w http.ResponseWriter, msg string) {
	reply(w, http.StatusBadRequest, map[string]string{"error": msg})
}

func reply(w http.ResponseWriter, code int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(body)
}

func nonEmpty(v, fallback string) string {
	if v == "" {
		return fallback
	}
	return v
}
