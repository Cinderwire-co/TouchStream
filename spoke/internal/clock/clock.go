// Package clock is the spoke's time: its time zone, where the box is (looked up on the internet
// from its address there, unless the hub sets one: a VPN can put it somewhere else), and whether
// the clock's right yet. The box keeps no time unplugged: it starts in 2021, until ntpd sets it.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package clock

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"
)

// Lookups answer with the time zone of the address a request comes from, as plain text
// ("America/Chicago"). Each is tried in turn.
var Lookups = []string{"https://ipinfo.io/timezone", "https://ipapi.co/timezone/"}

// Zone sets the local time zone to name (an IANA one, like America/Chicago). (The spoke carries
// the time zone database itself: the box has none.)
func Zone(name string) error {
	loc, err := time.LoadLocation(name)
	if err != nil {
		return err
	}
	time.Local = loc
	return nil
}

// Valid says whether name is a time zone ("" and "Local" aren't, though LoadLocation takes them).
func Valid(name string) bool {
	if name == "" || name == "Local" {
		return false
	}
	_, err := time.LoadLocation(name)
	return err == nil
}

// Set says whether the clock's been set: it's not 2021 any more.
func Set() bool { return time.Now().Year() >= 2025 }

// WaitSet waits up to most for the clock to be set, and says whether it was.
func WaitSet(ctx context.Context, most time.Duration) bool {
	end := time.Now().Add(most) // (it jumps when it's set: past this, which is fine)
	for !Set() && time.Now().Before(end) {
		select {
		case <-ctx.Done():
			return false
		case <-time.After(500 * time.Millisecond):
		}
	}
	return Set()
}

// Lookup asks the internet which time zone the box is in, from its address there.
func Lookup(ctx context.Context) (string, error) {
	err := errors.New("no lookups")
	for _, url := range Lookups {
		var zone string
		if zone, err = lookup(ctx, url); err == nil {
			return zone, nil
		}
	}
	return "", err
}

func lookup(ctx context.Context, url string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("User-Agent", "TouchStream-spoke")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 200))
	zone := strings.TrimSpace(string(body))
	if resp.StatusCode != http.StatusOK || !Valid(zone) {
		return "", errors.New(url + ": no time zone in its answer")
	}
	return zone, nil
}
