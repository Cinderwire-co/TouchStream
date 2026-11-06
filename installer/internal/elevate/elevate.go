// Package elevate runs this program again with admin rights, for the one thing that needs them:
// writing a card. The app itself stays an ordinary app (installing the hub for the person
// using it, not for an admin account); the copy run as admin reports back through a file.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package elevate

import (
	"encoding/json"
	"errors"
	"os"
	"time"
)

// ErrCanceled: the person said no to the password or permission prompt.
var ErrCanceled = errors.New("the card wasn't written: it needs your permission (an admin password) to write a card")

// Progress is what the copy run as admin reports, as JSON in a file the app watches.
type Progress struct {
	Phase string `json:"phase"` // writing, checking, done or failed
	Done  int64  `json:"done"`
	Total int64  `json:"total"`
	Error string `json:"error,omitempty"`
}

// Report writes progress to path, whole (a reader never sees half of it).
func Report(path string, p Progress) {
	data, _ := json.Marshal(p)
	if os.WriteFile(path+".tmp", data, 0o644) == nil {
		_ = os.Rename(path+".tmp", path)
	}
}

// Watch reads progress from path every interval until stop is closed.
func Watch(path string, interval time.Duration, stop <-chan struct{}, each func(Progress)) {
	tick := time.NewTicker(interval)
	defer tick.Stop()
	for {
		select {
		case <-stop:
			return
		case <-tick.C:
			var p Progress
			if data, err := os.ReadFile(path); err == nil && json.Unmarshal(data, &p) == nil {
				each(p)
			}
		}
	}
}
