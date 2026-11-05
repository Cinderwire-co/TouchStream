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
}

func (c Config) Paired() bool { return c.HubURL != "" && c.SpokeID != "" && c.HubToken != "" }

// Store is shared with other processes (touchstream-lite pair writes it while the spoke runs),
// so it reads the file again whenever it changes.
type Store struct {
	path  string
	mu    sync.Mutex
	cfg   Config
	mtime time.Time
}

// Open reads <dir>/config.json, or starts one named after the host.
func Open(dir string) (*Store, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	s := &Store{path: filepath.Join(dir, "config.json")}
	if err := s.load(); errors.Is(err, fs.ErrNotExist) {
		s.cfg.DeviceName, _ = os.Hostname()
	} else if err != nil {
		return nil, err
	}
	return s, nil
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
