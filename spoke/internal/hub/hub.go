// Package hub links the spoke to its TouchStream hub: pairing, and a heartbeat that reports
// the spoke's status and brings back what the hub wants (off / preview / live, where to send
// the stream, whether to record).
//
// The spoke always calls the hub, never the other way round, so it works behind any router.
// If the hub can't be reached the spoke carries on as it was: a blip never stops a stream.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package hub

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/config"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/direct"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/update"
)

const HeartbeatEvery = 3 * time.Second

// Stream is where the hub wants the video sent.
type Stream struct {
	Host        string `json:"host"`
	Port        int    `json:"port"`
	StreamID    string `json:"streamid"`
	LatencyMS   int    `json:"latency_ms"`
	BitrateKbps int    `json:"bitrate_kbps"`
}

// Desired is what the hub asks of the spoke on every heartbeat, and the schedule the spoke keeps
// in case the hub goes out of reach.
type Desired struct {
	Name        string        `json:"name"`
	Location    string        `json:"location"`
	Mode        string        `json:"mode"` // off, preview or live
	RecordLocal bool          `json:"record_local"`
	Stream      *Stream       `json:"stream"`
	Commands    []string      `json:"commands"`
	Framerate   string        `json:"framerate"`
	TimeZone    string        `json:"time_zone"` // "": where the spoke finds itself to be
	Update      *update.Offer `json:"update"`    // a newer spoke, when the hub has one for it and it's idle
	Viewers     *int          `json:"viewers"`   // watching on YouTube, while it's live (nil: not known)
	direct.Plan
}

type Error struct {
	Code   int
	Detail string
}

func (e *Error) Error() string { return fmt.Sprintf("Hub said %d %s", e.Code, e.Detail) }

var localHost = regexp.MustCompile(`^[\d.]+(:\d+)?$`)

// NormalizeURL: 'hub.example.com' -> https://..., '192.168.1.20:8080' or 'name.local' -> http://...
func NormalizeURL(address string) string {
	address = strings.TrimRight(strings.TrimSpace(address), "/")
	if strings.Contains(address, "://") {
		return address
	}
	host := strings.SplitN(address, "/", 2)[0]
	name := strings.SplitN(host, ":", 2)[0]
	if localHost.MatchString(host) || strings.Contains(host, ":") || strings.HasSuffix(name, ".local") ||
		strings.HasPrefix(host, "localhost") {
		return "http://" + address
	}
	return "https://" + address
}

var client = &http.Client{Timeout: 10 * time.Second}

// Request sends JSON (or a body as is) and decodes the JSON reply into out.
func Request(ctx context.Context, method, url string, body any, headers map[string]string, out any) error {
	var reader io.Reader
	switch b := body.(type) {
	case nil:
	case io.Reader:
		reader = b
	default:
		data, err := json.Marshal(b)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, url, reader)
	if err != nil {
		return err
	}
	if sized, ok := body.(interface{ Size() int64 }); ok {
		req.ContentLength = sized.Size()
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode >= 300 {
		var detail struct {
			Detail any `json:"detail"`
		}
		_ = json.Unmarshal(data, &detail)
		return &Error{resp.StatusCode, fmt.Sprint(detail.Detail)}
	}
	if out == nil {
		return nil
	}
	return json.Unmarshal(data, out)
}

// Auth is the headers that identify this spoke to its hub.
func Auth(c config.Config) map[string]string {
	return map[string]string{"X-Spoke-Id": c.SpokeID, "Authorization": "Bearer " + c.HubToken}
}

// Pair joins a hub with the code its dashboard shows under Add spoke.
func Pair(ctx context.Context, store *config.Store, address, code, version string) error {
	url := NormalizeURL(address)
	var resp struct {
		SpokeID string `json:"spoke_id"`
		Token   string `json:"token"`
	}
	body := map[string]string{"code": code, "name": store.Get().DeviceName, "version": version}
	if err := Request(ctx, "POST", url+"/api/spoke/pair", body, nil, &resp); err != nil {
		return err
	}
	return store.Update(func(c *config.Config) { c.HubURL, c.SpokeID, c.HubToken = url, resp.SpokeID, resp.Token })
}

func Unpair(store *config.Store) error {
	return store.Update(func(c *config.Config) { c.HubURL, c.SpokeID, c.HubToken = "", "", "" })
}

// Link runs the heartbeat. OnDesired gets nil when the hub has forgotten this spoke.
type Link struct {
	Store     *config.Store
	Status    func() map[string]any
	OnDesired func(*Desired)

	mu        sync.Mutex
	connected bool
	tried     bool // a heartbeat has been answered or has failed since it started
	lastErr   string
	lastOK    time.Time
}

func (l *Link) Run(ctx context.Context) {
	l.mu.Lock()
	l.lastOK = time.Now() // never reached at all counts from now
	l.mu.Unlock()
	tick := time.NewTicker(HeartbeatEvery)
	defer tick.Stop()
	for {
		if c := l.Store.Get(); c.Paired() {
			l.beat(ctx, c)
		} else {
			l.set(false, "")
		}
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
		}
	}
}

func (l *Link) beat(ctx context.Context, c config.Config) {
	ctx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	var desired Desired
	err := Request(ctx, "POST", c.HubURL+"/api/spoke/heartbeat", l.Status(), Auth(c), &desired)
	var hubErr *Error
	switch {
	case errors.As(err, &hubErr) && hubErr.Code == 401:
		// The hub removed this spoke: back to unpaired
		log.Print("Hub no longer knows this spoke; unpairing")
		_ = Unpair(l.Store)
		l.set(false, err.Error())
		l.OnDesired(nil)
	case err != nil:
		l.set(false, err.Error())
	default:
		l.set(true, "")
		l.OnDesired(&desired)
	}
}

func (l *Link) set(connected bool, err string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.connected, l.lastErr, l.tried = connected, err, l.tried || connected || err != ""
	if connected {
		l.lastOK = time.Now()
	}
}

// State is what the link is doing: connecting (nothing heard yet), connected, or unreachable.
func (l *Link) State() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	switch {
	case l.connected:
		return "connected"
	case !l.tried:
		return "connecting"
	}
	return "unreachable"
}

// LastOK is when the hub last answered a heartbeat.
func (l *Link) LastOK() time.Time {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.lastOK
}

func (l *Link) Connected() (bool, string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.connected, l.lastErr
}
