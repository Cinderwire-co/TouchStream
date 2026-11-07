// Package config keeps the spoke's settings in one JSON file in its data folder.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package config

import (
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type Config struct {
	DeviceName string `json:"device_name"`
	Location   string `json:"location"`
	HubURL     string `json:"hub_url,omitempty"`
	SpokeID    string `json:"spoke_id,omitempty"`
	HubToken   string `json:"hub_token,omitempty"`
	Framerate  string `json:"framerate,omitempty"`
	TimeZone   string `json:"time_zone,omitempty"`  // set in the hub; none: FoundZone
	FoundZone  string `json:"found_zone,omitempty"` // where the box is, looked up on the internet
	// The stream with no hub, set on the setup page (with a hub, the hub's settings for it apply)
	Resolution  string `json:"resolution,omitempty"` // 1080p or 720p
	BitrateKbps int    `json:"bitrate_kbps,omitempty"`
	// Where Go live streams with no hub, added on the setup page (the file is the spoke's alone)
	Destinations []Destination `json:"destinations,omitempty"`
	// The installer's settings from the card, once they're applied (provision.Apply)
	Provisioned string `json:"provisioned,omitempty"`
}

type Destination struct {
	Name string `json:"name"`
	URL  string `json:"url"`
	Key  string `json:"key"`
}

func (c Config) Paired() bool { return c.HubURL != "" && c.SpokeID != "" && c.HubToken != "" }

// Store is shared with other processes (touchstream-spoke pair writes it while the spoke runs),
// so it reads the file again whenever it changes.
type Store struct {
	path  string
	mu    sync.Mutex
	cfg   Config
	mtime time.Time
}

// Zone is the spoke's time zone: the hub's choice, or where it found itself to be ("": not known yet).
func (c Config) Zone() string {
	if c.TimeZone != "" {
		return c.TimeZone
	}
	return c.FoundZone
}

// Open reads <dir>/config.json, or starts one with a name of its own (DefaultName).
func Open(dir string) (*Store, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	s := &Store{path: filepath.Join(dir, "config.json")}
	if err := s.load(); errors.Is(err, fs.ErrNotExist) {
		s.cfg.DeviceName = DefaultName()
	} else if err != nil {
		return nil, err
	}
	return s, nil
}

// DefaultName is a spoke's name until it's given one: "Spoke" and the last four of its chip's serial
// number, so that two new ones can be told apart (and a box keeps it, installed again); off the box,
// the host's name.
func DefaultName() string {
	if data, err := os.ReadFile("/proc/cpuinfo"); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			if k, v, ok := strings.Cut(line, ":"); ok && strings.TrimSpace(k) == "Serial" {
				if v = strings.ToUpper(strings.TrimSpace(v)); len(v) >= 4 && strings.Trim(v, "0") != "" {
					return "Spoke " + v[len(v)-4:]
				}
			}
		}
	}
	name, _ := os.Hostname()
	return name
}

func (s *Store) load() error {
	info, err := os.Stat(s.path)
	if err != nil {
		return err
	}
	if info.ModTime().Equal(s.mtime) {
		return nil
	}
	data, err := os.ReadFile(s.path)
	if err != nil {
		return err
	}
	var cfg Config
	if err := json.Unmarshal(data, &cfg); err != nil {
		return err
	}
	s.cfg, s.mtime = cfg, info.ModTime()
	return nil
}

func (s *Store) Get() Config {
	s.mu.Lock()
	defer s.mu.Unlock()
	_ = s.load() // keep the last good copy if the file can't be read
	return s.cfg
}

// Update changes the settings and saves them; a power cut mid-save leaves the old file.
func (s *Store) Update(change func(*Config)) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	_ = s.load()
	next := s.cfg
	change(&next)
	data, err := json.MarshalIndent(next, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tmp, s.path); err != nil {
		return err
	}
	s.cfg = next
	if info, err := os.Stat(s.path); err == nil {
		s.mtime = info.ModTime()
	}
	return nil
}
