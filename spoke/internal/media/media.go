// Package media is where the spoke's stream comes from: MPEG-TS with H.264 video and AAC
// audio, in 188-byte packets, handed on to the uplink and the recorder.
//
// On the PicoKVM the RV1106's hardware encoder makes it from the HDMI input (still to come).
// In the emulator a replay of a ready-made clip stands in: the hardware encodes at almost no
// CPU cost, so replaying an encoded stream puts the same load on the rest of the spoke.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package media

import (
	"context"
	"sync"
	"sync/atomic"
)

// Signal is what's coming into the HDMI input.
type Signal struct {
	Present bool
	Width   int
	Height  int
	FPS     float64
}

// NoSignal is the box's HDMI input until its capture is built: never a signal.
type NoSignal struct{}

func (NoSignal) Run(ctx context.Context, emit func([]byte)) error { <-ctx.Done(); return nil }
func (NoSignal) Signal() Signal                                   { return Signal{} }

type Source interface {
	// Run makes the stream until ctx ends, calling emit with whole packets (a multiple of 188 bytes).
	Run(ctx context.Context, emit func([]byte)) error
	Signal() Signal
}

// Fanout hands each chunk to every subscriber. A subscriber that falls behind loses chunks
// rather than holding up the others: the recorder must never stall the live stream.
type Fanout struct {
	mu      sync.Mutex
	subs    []chan []byte
	dropped atomic.Int64
}

func (f *Fanout) Subscribe(depth int) <-chan []byte {
	ch := make(chan []byte, depth)
	f.mu.Lock()
	f.subs = append(f.subs, ch)
	f.mu.Unlock()
	return ch
}

func (f *Fanout) Emit(chunk []byte) {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, ch := range f.subs {
		select {
		case ch <- chunk:
		default:
			f.dropped.Add(1)
		}
	}
}

// Dropped counts chunks a slow subscriber missed.
func (f *Fanout) Dropped() int64 { return f.dropped.Load() }
