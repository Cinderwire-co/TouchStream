// Package discovery lets a hub on the same network find and adopt the spoke, as with the Pi
// spoke (api.py): a beacon every few seconds, /info to describe it, /adopt to pair it.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package discovery

import (
	"context"
	"encoding/json"
	"log"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/Cinderwire-co/TouchStream/lite/internal/config"
)

const (
	HTTPPort    = 6077
	BeaconPort  = 9999
	beaconEvery = 5 * time.Second
	Model       = "picokvm"
)

// Routes adds /info and /adopt to mux.
func Routes(mux *http.ServeMux, store *config.Store, version string) {
	mux.HandleFunc("GET /info", func(w http.ResponseWriter, r *http.Request) {
		c := store.Get()
		id := c.SpokeID
		if id == "" {
			id = "unadopted"
		}
		reply(w, http.StatusOK, map[string]any{
			"device_id": id, "device_name": c.DeviceName, "ip": LocalIP(), "model": Model,
			"status": "ready", "version": version, "paired": c.HubToken != "",
		})
	})
	mux.HandleFunc("POST /adopt", func(w http.ResponseWriter, r *http.Request) {
		// A hub on the LAN adopting this spoke hands over its address and credentials. Only an
		// unpaired spoke accepts; otherwise only the current hub may adopt it again.
		var body struct {
			HubURL  string `json:"hub_url"`
			SpokeID string `json:"spoke_id"`
			Token   string `json:"token"`
		}
		current := store.Get().HubToken
		if given := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "); current != "" && given != current {
			reply(w, http.StatusForbidden, map[string]any{"status": "error", "error": "Already paired with a hub"})
			return
		}
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10)).Decode(&body); err != nil ||
			body.HubURL == "" || body.SpokeID == "" || body.Token == "" {
			reply(w, http.StatusBadRequest, map[string]any{"status": "error", "error": "hub_url, spoke_id and token are needed"})
			return
		}
		if err := store.Update(func(c *config.Config) { c.HubURL, c.SpokeID, c.HubToken = body.HubURL, body.SpokeID, body.Token }); err != nil {
			reply(w, http.StatusInternalServerError, map[string]any{"status": "error", "error": err.Error()})
			return
		}
		log.Printf("Adopted by hub at %s", body.HubURL)
		reply(w, http.StatusOK, map[string]any{"status": "ok", "saved": true})
	})
}

func reply(w http.ResponseWriter, code int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(body)
}

// Beacon announces the spoke on the LAN until ctx ends.
func Beacon(ctx context.Context, store *config.Store) {
	conn, err := net.DialUDP("udp4", nil, &net.UDPAddr{IP: net.IPv4bcast, Port: BeaconPort})
	if err != nil {
		log.Printf("Beacon: %v", err)
		return
	}
	defer conn.Close()
	for {
		c := store.Get()
		id := c.SpokeID
		if id == "" {
			id = "unadopted"
		}
		payload, _ := json.Marshal(map[string]string{"device_name": c.DeviceName, "device_id": id})
		_, _ = conn.Write(payload)
		select {
		case <-ctx.Done():
			return
		case <-time.After(beaconEvery):
		}
	}
}

// LocalIP is the address other devices on the network reach this one at.
func LocalIP() string {
	conn, err := net.Dial("udp4", "192.0.2.1:9") // no packet is sent: it only picks the outgoing interface
	if err != nil {
		return ""
	}
	defer conn.Close()
	return conn.LocalAddr().(*net.UDPAddr).IP.String()
}
