// Package uplink sends the stream to the hub over SRT: it connects when the hub gives it a
// target, reconnects by itself after a drop, and throws the stream away while there's nowhere
// to send it.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package uplink

import (
	"context"
	"fmt"
	"log"
	"sync"
	"time"

	srt "github.com/datarhei/gosrt"
)

const retryAfter = 2 * time.Second

type Target struct {
	Host      string
	Port      int
	StreamID  string
	LatencyMS int
}

type Uplink struct {
	mu     sync.Mutex
	target *Target
	conn   srt.Conn
	err    string
	change chan struct{}
}

func New() *Uplink { return &Uplink{change: make(chan struct{}, 1)} }

// Set points the uplink at a new target (nil: stop sending). The same target changes nothing.
func (u *Uplink) Set(t *Target) {
	u.mu.Lock()
	same := (t == nil && u.target == nil) || (t != nil && u.target != nil && *t == *u.target)
	if !same {
		u.target, u.err = t, ""
		if u.conn != nil {
			u.conn.Close()
			u.conn = nil
		}
	}
	u.mu.Unlock()
	if !same {
		select {
		case u.change <- struct{}{}:
		default:
		}
	}
}

// State is what the hub dashboard shows: off, connecting, retrying or sending.
func (u *Uplink) State() (string, string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	switch {
	case u.target == nil:
		return "off", ""
	case u.conn == nil && u.err != "":
		return "retrying", u.err
	case u.conn == nil:
		return "connecting", ""
	}
	return "sending", ""
}

// Run sends chunks from in until ctx ends.
func (u *Uplink) Run(ctx context.Context, in <-chan []byte) {
	go u.connect(ctx)
	for {
		select {
		case <-ctx.Done():
			u.Set(nil)
			return
		case chunk := <-in:
			u.mu.Lock()
			conn := u.conn
			u.mu.Unlock()
			if conn == nil {
				continue
			}
			if _, err := conn.Write(chunk); err != nil {
				u.drop(conn, err)
			}
		}
	}
}

// connect keeps a connection open to the current target.
func (u *Uplink) connect(ctx context.Context) {
	for ctx.Err() == nil {
		u.mu.Lock()
		target, conn := u.target, u.conn
		u.mu.Unlock()
		if target != nil && conn == nil {
			if err := u.dial(target); err != nil {
				u.mu.Lock()
				u.err = err.Error()
				u.mu.Unlock()
				log.Printf("Uplink to %s:%d: %v", target.Host, target.Port, err)
			}
		}
		select {
		case <-ctx.Done():
		case <-u.change:
		case <-time.After(retryAfter):
		}
	}
}

func (u *Uplink) dial(t *Target) error {
	cfg := srt.DefaultConfig()
	cfg.StreamId = t.StreamID
	cfg.Latency = time.Duration(max(t.LatencyMS, 120)) * time.Millisecond
	cfg.ConnectionTimeout = 3 * time.Second
	conn, err := srt.Dial("srt", fmt.Sprintf("%s:%d", t.Host, t.Port), cfg)
	if err != nil {
		return err
	}
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.target == nil || *u.target != *t { // the hub changed its mind while we were dialing
		conn.Close()
		return nil
	}
	u.conn, u.err = conn, ""
	log.Printf("Uplink to %s:%d started", t.Host, t.Port)
	return nil
}

func (u *Uplink) drop(conn srt.Conn, err error) {
	u.mu.Lock()
	if u.conn == conn {
		u.conn, u.err = nil, err.Error()
		log.Printf("Uplink dropped: %v", err)
	}
	u.mu.Unlock()
	conn.Close()
	select {
	case u.change <- struct{}{}:
	default:
	}
}
