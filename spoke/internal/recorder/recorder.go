// Package recorder keeps the spoke's own copy of what it streams, on the microSD card, and
// uploads it to the hub as it's written:
//
//	<dir>/<session>/part-00000.ts ...   5-minute MPEG-TS parts, each starting on a keyframe
//	<dir>/<session>/.uploaded           parts the hub has confirmed
//	<dir>/<session>/.complete           recording finished
//	<dir>/<session>/.merged             the hub has joined the parts into one MP4
//	<dir>/<session>/keyframes.txt       when each keyframe comes, in seconds: where a trim can start
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package recorder

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/ts"
)

const (
	SegmentLength = 5 * time.Minute
	SessionLayout = "20060102-150405" // session names are the spoke's local start time
)

type Recorder struct {
	Dir string

	mu       sync.Mutex
	session  string
	file     *os.File
	part     int
	opened   time.Time
	tables   ts.Tables
	stopping chan struct{} // closed once a stopping session's last frame is written
	firstKey int64         // the first keyframe's PTS: keyframe times count from it (-1: none yet)
	keys     *os.File
}

// Start begins a session (if one isn't already running) and returns its folder.
func (r *Recorder) Start() (string, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.stopping != nil {
		r.finish() // started again before the last one had finished: that one ends here
	}
	if r.session != "" {
		return r.session, nil
	}
	path := filepath.Join(r.Dir, time.Now().Format(SessionLayout))
	if err := os.MkdirAll(path, 0o755); err != nil {
		return "", err
	}
	r.session, r.part, r.firstKey = path, 0, -1
	log.Printf("Recording locally to %s", path)
	return path, nil
}

// Stop ends the session after the frame being written, so the last part doesn't end halfway
// through one, and marks it finished, ready for its last upload.
func (r *Recorder) Stop() {
	r.mu.Lock()
	if r.session == "" {
		r.mu.Unlock()
		return
	}
	if r.file == nil {
		r.finish()
		r.mu.Unlock()
		return
	}
	done := make(chan struct{})
	r.stopping = done
	r.mu.Unlock()
	select {
	case <-done:
	case <-time.After(time.Second): // the stream has stopped too: end where it is
		r.mu.Lock()
		if r.stopping == done {
			r.finish()
		}
		r.mu.Unlock()
	}
}

func (r *Recorder) finish() {
	r.closePart()
	if r.keys != nil {
		r.keys.Close()
		r.keys = nil
	}
	_ = os.WriteFile(filepath.Join(r.session, ".complete"), nil, 0o644)
	log.Printf("Local recording finished: %s", r.session)
	r.session = ""
	if r.stopping != nil {
		close(r.stopping)
		r.stopping = nil
	}
}

func (r *Recorder) Recording() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.session != ""
}

// Run writes chunks from in while a session is open; the stream's tables are tracked
// all the time so a session can start on the next keyframe.
func (r *Recorder) Run(in <-chan []byte) {
	for chunk := range in {
		r.write(chunk)
	}
}

func (r *Recorder) write(chunk []byte) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for at := 0; at+ts.PacketSize <= len(chunk); at += ts.PacketSize {
		p := chunk[at : at+ts.PacketSize]
		r.tables.Note(p)
		if r.session == "" {
			continue
		}
		if r.stopping != nil && ts.PID(p) == r.tables.VideoPID && ts.Start(p) {
			r.finish() // the last frame is whole: end before the next one
			continue
		}
		if r.tables.Ready(p) && (r.file == nil || time.Since(r.opened) >= SegmentLength) {
			if err := r.openPart(); err != nil {
				log.Printf("Recording: %v", err)
				continue
			}
		}
		if r.file != nil {
			if _, err := r.file.Write(p); err != nil {
				log.Printf("Recording: %v", err)
				r.closePart()
			} else if r.tables.Ready(p) {
				r.noteKeyframe(p)
			}
		}
	}
}

// noteKeyframe writes down when a keyframe comes, counted from the recording's first.
func (r *Recorder) noteKeyframe(p []byte) {
	pts, ok := ts.PTS(p)
	if !ok {
		return
	}
	if r.firstKey < 0 {
		r.firstKey = pts
	}
	if r.keys == nil {
		f, err := os.OpenFile(filepath.Join(r.session, "keyframes.txt"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		if err != nil {
			return
		}
		r.keys = f
	}
	since := (pts - r.firstKey + 1<<33) % (1 << 33) // (PTS wraps after about 26 hours)
	fmt.Fprintf(r.keys, "%.3f\n", float64(since)/ts.PTSHz)
}

// Keyframes is when each keyframe comes in a recording, in seconds from its start (none for one
// made before they were noted).
func Keyframes(s Session) []float64 {
	data, err := os.ReadFile(filepath.Join(s.Path, "keyframes.txt"))
	if err != nil {
		return nil
	}
	var out []float64
	for _, line := range strings.Fields(string(data)) {
		if v, err := strconv.ParseFloat(line, 64); err == nil {
			out = append(out, v)
		}
	}
	return out
}

// openPart starts the next part with the stream's tables, so it plays on its own.
func (r *Recorder) openPart() error {
	r.closePart()
	if r.partExists() {
		r.part++
	}
	f, err := os.Create(filepath.Join(r.session, fmt.Sprintf("part-%05d.ts", r.part)))
	if err != nil {
		return err
	}
	r.file, r.opened = f, time.Now()
	_, err = f.Write(append(append([]byte{}, r.tables.PAT...), r.tables.PMT...))
	return err
}

func (r *Recorder) partExists() bool {
	_, err := os.Stat(filepath.Join(r.session, fmt.Sprintf("part-%05d.ts", r.part)))
	return err == nil
}

func (r *Recorder) closePart() {
	if r.file != nil {
		r.file.Close()
		r.file = nil
	}
}

// Session is one recording on the card.
type Session struct {
	Name      string
	Path      string
	Start     time.Time
	Duration  time.Duration
	Parts     []string // part file names, in order
	Size      int64
	Complete  bool
	OnHub     bool // the hub has it all
	Uploaded  map[string]bool
	Recording bool
}

func (r *Recorder) Sessions() []Session {
	dirs, _ := os.ReadDir(r.Dir)
	r.mu.Lock()
	current := r.session
	r.mu.Unlock()
	var out []Session
	for _, d := range dirs {
		if !d.IsDir() {
			continue
		}
		start, err := time.ParseInLocation(SessionLayout, d.Name(), time.Local)
		if err != nil {
			continue
		}
		s := Session{Name: d.Name(), Path: filepath.Join(r.Dir, d.Name()), Start: start, Uploaded: map[string]bool{}}
		s.Recording = s.Path == current
		s.Complete = exists(filepath.Join(s.Path, ".complete"))
		s.OnHub = exists(filepath.Join(s.Path, ".merged"))
		if done, err := os.ReadFile(filepath.Join(s.Path, ".uploaded")); err == nil {
			for _, name := range strings.Fields(string(done)) {
				s.Uploaded[name] = true
			}
		}
		parts, _ := filepath.Glob(filepath.Join(s.Path, "part-*.ts"))
		sort.Strings(parts)
		end := start
		for _, p := range parts {
			if info, err := os.Stat(p); err == nil {
				s.Parts = append(s.Parts, filepath.Base(p))
				s.Size += info.Size()
				end = later(end, info.ModTime())
			}
		}
		if info, err := os.Stat(filepath.Join(s.Path, ".complete")); err == nil {
			end = info.ModTime()
		}
		if len(s.Parts) == 0 {
			continue // started, but no keyframe arrived to begin a part
		}
		s.Duration = max(0, end.Sub(start))
		out = append(out, s)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// Find returns the session called name.
func (r *Recorder) Find(name string) (Session, bool) {
	for _, s := range r.Sessions() {
		if s.Name == name {
			return s, true
		}
	}
	return Session{}, false
}

func later(a, b time.Time) time.Time {
	if b.After(a) {
		return b
	}
	return a
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}
