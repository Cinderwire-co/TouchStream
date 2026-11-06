package media

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"sync/atomic"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/ts"
)

// Replay plays an MPEG-TS clip in real time, over and over, as if an encoder were making it
// live: paced by the clip's own clock, with timestamps that keep counting up across loops.
type Replay struct {
	Path   string
	Format Signal // what the clip is, reported as the "HDMI signal"

	unplugged atomic.Bool
}

const chunkPackets = 7 // 1316 bytes: what fits in one SRT packet

func (r *Replay) Signal() Signal {
	if r.unplugged.Load() {
		return Signal{}
	}
	return r.Format
}

// Plug pretends the HDMI cable went in or came out: while it's out, nothing is sent.
func (r *Replay) Plug(in bool) { r.unplugged.Store(!in) }

func (r *Replay) Run(ctx context.Context, emit func([]byte)) error {
	var offset int64 // added to every clock: how far earlier loops have run
	var start time.Time
	var first int64 = -1 // the first PCR played, after shifting
	for ctx.Err() == nil {
		span, err := r.play(ctx, offset, func(pcr int64) {
			if first < 0 {
				first, start = pcr, time.Now()
			}
			wait := time.Until(start.Add(time.Duration((pcr - first) * int64(time.Second) / ts.PCRHz)))
			if wait > 0 {
				select {
				case <-ctx.Done():
				case <-time.After(wait):
				}
			}
		}, emit)
		if err != nil {
			return err
		}
		offset += span
	}
	return ctx.Err()
}

// play sends the clip once and returns how long it runs (in PCR ticks): from its first frame
// to the end of its last, in whichever stream runs longest (often the sound), so every stream's
// next loop starts after its last one ends.
func (r *Replay) play(ctx context.Context, offset int64, pace func(int64), emit func([]byte)) (int64, error) {
	f, err := os.Open(r.Path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	in := bufio.NewReaderSize(f, 64*1024)
	type times struct{ first, last, step int64 } // decode times; step is the last frame's length
	streams := map[uint16]*times{}
	paced := false
	for ctx.Err() == nil {
		chunk := make([]byte, chunkPackets*ts.PacketSize)
		n, err := io.ReadFull(in, chunk)
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			chunk = chunk[:n-n%ts.PacketSize]
		} else if err != nil {
			return 0, err
		}
		for at := 0; at < len(chunk); at += ts.PacketSize {
			p := chunk[at : at+ts.PacketSize]
			if p[0] != ts.SyncByte {
				return 0, fmt.Errorf("%s isn't 188-byte MPEG-TS", r.Path)
			}
			if dts, ok := ts.DTS(p); ok {
				if t := streams[ts.PID(p)]; t == nil {
					streams[ts.PID(p)] = &times{dts, dts, 0}
				} else if dts > t.last {
					t.step, t.last = dts-t.last, dts
				}
			}
			pcr, hasPCR := ts.PCR(p)
			ts.Shift(p, offset)
			if hasPCR {
				paced = true
				pace(pcr + offset)
			}
		}
		if len(chunk) > 0 && !r.unplugged.Load() {
			emit(chunk)
		}
		if err != nil {
			break
		}
	}
	if !paced || len(streams) == 0 {
		return 0, fmt.Errorf("%s needs a clock (PCR) to pace it by, and a stream", r.Path)
	}
	first, end := int64(math.MaxInt64), int64(0)
	for _, t := range streams {
		first, end = min(first, t.first), max(end, t.last+t.step)
	}
	return (end - first) * 300, nil
}
