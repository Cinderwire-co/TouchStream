// Package direct streams straight to YouTube when the hub can't be reached. Every heartbeat the
// hub sends the spoke its upcoming broadcasts, with the stream address and key for each, plus its
// own stream-key destinations; the spoke keeps that copy on the card. If the hub stays out of
// reach, the spoke runs its own schedule: it starts a due broadcast (or keeps a live one going)
// by streaming to YouTube itself, and stops at the end time. It uses the same stream key, so
// YouTube sees a reconnect, not a new stream; when the hub is back it takes over again.
//
// The stream is repackaged for YouTube (MPEG-TS to FLV over RTMP), not re-encoded, so it costs
// the box little.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package direct

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/ts"
)

const (
	HubTimeout = 30 * time.Second // without the hub this long, the spoke acts on its own
	lead       = 10 * time.Second // start this early, like the hub does
	openEnded  = 4 * time.Hour    // how long a broadcast without an end time may run
	retryAfter = 5 * time.Second
)

// Target is somewhere to stream: an RTMP(S) address and its stream key.
type Target struct {
	URL  string `json:"url"`
	Key  string `json:"key"`
	Name string `json:"name"`
}

type Event struct {
	ID        int      `json:"id"`
	Title     string   `json:"title"`
	StartAt   float64  `json:"start_at"`
	EndAt     *float64 `json:"end_at"`
	AutoStart *bool    `json:"auto_start"`
	Targets   []Target `json:"targets"`
}

// Plan is the hub's latest schedule and settings, kept for when it's out of reach.
type Plan struct {
	Schedule        []Event  `json:"schedule"`
	Destinations    []Target `json:"direct_destinations"`
	BitrateKbps     int      `json:"bitrate_kbps"`
	RecordLocalPref bool     `json:"record_local_pref"`
	SavedAt         float64  `json:"saved_at"`
}

func planPath(dir string) string { return filepath.Join(dir, "schedule.json") }

// Save keeps the plan on the card. It holds stream keys, so only the spoke may read it.
func Save(dir string, p Plan) error {
	p.SavedAt = float64(time.Now().Unix())
	data, err := json.Marshal(p)
	if err != nil {
		return err
	}
	tmp := planPath(dir) + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, planPath(dir))
}

func Load(dir string) Plan {
	var p Plan
	if data, err := os.ReadFile(planPath(dir)); err == nil {
		_ = json.Unmarshal(data, &p)
	}
	return p
}

// Due is the broadcast that should be on air now, if any.
func (p Plan) Due(now time.Time) *Event {
	t := float64(now.Unix())
	for i, e := range p.Schedule {
		end := e.StartAt + openEnded.Seconds()
		if e.EndAt != nil {
			end = *e.EndAt
		}
		if (e.AutoStart == nil || *e.AutoStart) && e.StartAt-lead.Seconds() <= t && t < end {
			return &p.Schedule[i]
		}
	}
	return nil
}

// Stream relays the spoke's stream to RTMP(S) targets while it has some.
type Stream struct {
	mu      sync.Mutex
	gen     int // which ffmpeg this is (each starts at a keyframe)
	targets []Target
	in      io.WriteCloser
	cmd     *exec.Cmd
	sending bool // ffmpeg has connected and is sending, not just started
	err     string
	change  chan struct{}
}

func New() *Stream { return &Stream{change: make(chan struct{}, 1)} }

// Set changes where it streams; nil stops it. The same targets change nothing.
func (s *Stream) Set(targets []Target) {
	s.mu.Lock()
	same := slices.Equal(targets, s.targets)
	if !same {
		s.targets, s.err = targets, ""
		s.stopLocked()
	}
	s.mu.Unlock()
	if !same {
		select {
		case s.change <- struct{}{}:
		default:
		}
	}
}

func (s *Stream) Active() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.targets) > 0
}

// State is what it's doing: off, connecting (to YouTube), sending, or retrying after a failure.
func (s *Stream) State() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	switch {
	case len(s.targets) == 0:
		return "off"
	case s.sending:
		return "sending"
	case s.cmd == nil && s.err != "":
		return "retrying"
	}
	return "connecting"
}

// Run feeds the stream from in to ffmpeg while there are targets, restarting it after a failure.
func (s *Stream) Run(ctx context.Context, in <-chan []byte) {
	go s.keep(ctx)
	var start startPoint
	var gen int
	for {
		select {
		case <-ctx.Done():
			s.Set(nil)
			return
		case chunk := <-in:
			s.mu.Lock()
			w, g := s.in, s.gen
			s.mu.Unlock()
			if g != gen { // a new ffmpeg: it starts at the next keyframe
				gen, start.started = g, false
			}
			if out := start.next(chunk); w != nil && out != nil {
				if _, err := w.Write(out); err != nil {
					s.fail(w, err)
				}
			}
		}
	}
}

// startPoint holds a stream back until a keyframe, then starts it with the stream's tables: an
// ffmpeg that joins part way through can't tell the picture's size otherwise (the box's, 4.4,
// gives up: "dimensions not set").
type startPoint struct {
	tables  ts.Tables
	started bool
}

// next is what of chunk to send: nothing until a keyframe begins, then the tables and the rest.
func (sp *startPoint) next(chunk []byte) []byte {
	from := -1
	for i := 0; i+ts.PacketSize <= len(chunk); i += ts.PacketSize {
		p := chunk[i : i+ts.PacketSize]
		sp.tables.Note(p)
		if !sp.started && from < 0 && sp.tables.Ready(p) {
			from = i
		}
	}
	switch {
	case sp.started:
		return chunk
	case from < 0:
		return nil
	}
	sp.started = true
	return append(append(append([]byte{}, sp.tables.PAT...), sp.tables.PMT...), chunk[from:]...)
}

func (s *Stream) keep(ctx context.Context) {
	for ctx.Err() == nil {
		s.mu.Lock()
		if len(s.targets) > 0 && s.cmd == nil {
			if err := s.startLocked(); err != nil {
				s.err = err.Error()
				log.Printf("Direct stream: %v", err)
			}
		}
		s.mu.Unlock()
		select {
		case <-ctx.Done():
		case <-s.change:
		case <-time.After(retryAfter):
		}
	}
}

// Args is the ffmpeg command that sends a stream on stdin to every target: copied, not re-encoded,
// and a target that fails doesn't stop the others. FLV wants its own codec tags (H.264 is 7, AAC
// 10, not MPEG-TS's 27 and 15), and AAC without the per-frame headers TS carries.
func Args(targets []Target) []string {
	outs := make([]string, len(targets))
	for i, t := range targets {
		outs[i] = "[f=flv:onfail=ignore:bsfs/a=aac_adtstoasc]" + strings.TrimRight(t.URL, "/") + "/" + t.Key
	}
	return []string{"-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", // so it says when it's sending
		"-fflags", "+genpts", "-f", "mpegts", "-i", "pipe:0",
		"-map", "0:v", "-map", "0:a?", "-c", "copy", "-tag:v", "7", "-tag:a", "10", "-f", "tee", strings.Join(outs, "|")}
}

// Sending reports whether a line of ffmpeg's -progress says it's connected and sending. The box's
// ffmpeg (4.4) gives the tee muxer no total_size, so the output clock moving counts too: ffmpeg
// writes nothing until its destinations have connected.
func Sending(line string) bool {
	key, value, _ := strings.Cut(line, "=")
	switch key {
	case "total_size":
		return value != "0" && value != "N/A"
	case "out_time_us", "out_time_ms": // (both are microseconds)
		n, err := strconv.ParseInt(value, 10, 64)
		return err == nil && n > 0
	}
	return false
}

func (s *Stream) startLocked() error {
	if _, err := exec.LookPath("ffmpeg"); err != nil {
		return errors.New("streaming straight to YouTube needs ffmpeg")
	}
	cmd := exec.Command("ffmpeg", Args(s.targets)...)
	in, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	progress, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	var stderr strings.Builder
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		return err
	}
	s.cmd, s.in, s.err, s.sending = cmd, in, "", false
	s.gen++
	go func() {
		lines := bufio.NewScanner(progress)
		for lines.Scan() {
			if Sending(lines.Text()) {
				s.mu.Lock()
				if s.cmd == cmd {
					s.sending = true
				}
				s.mu.Unlock()
			}
		}
	}()
	names := make([]string, len(s.targets))
	for i, t := range s.targets {
		names[i] = nonEmpty(t.Name, "destination")
	}
	log.Printf("Direct to %s started", strings.Join(names, ", "))
	go func() {
		err := cmd.Wait()
		s.mu.Lock()
		defer s.mu.Unlock()
		if s.cmd == cmd {
			s.cmd, s.in, s.sending = nil, nil, false
			s.err = fmt.Sprintf("%v %s", err, strings.TrimSpace(stderr.String()))
			log.Printf("Direct stream stopped: %s", s.err)
		}
	}()
	return nil
}

func (s *Stream) fail(w io.WriteCloser, err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.in == w {
		s.err = err.Error()
		s.stopLocked()
	}
}

func (s *Stream) stopLocked() {
	if s.cmd != nil {
		s.in.Close()
		_ = s.cmd.Process.Kill()
		s.cmd, s.in, s.sending = nil, nil, false
	}
}

func nonEmpty(v, fallback string) string {
	if v == "" {
		return fallback
	}
	return v
}
