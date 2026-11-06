// Package share puts a recording on a phone: the screen shows a QR code for a short-lived link
// to a page the spoke serves itself, so it works on the venue's network with no internet, to
// watch the recording, trim it and save it.
//
// Phones need a regular MP4 (they can't play the TS parts, or an MP4 still being written), so
// opening a recording makes one first. It's a copy, not a re-encode: about a minute per hour of
// video. A trim is copied out of that MP4 the same way, from the keyframe at or before its start.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package share

import (
	"bufio"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/assets"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/recorder"
)

const (
	linkLife = 30 * time.Minute // how long a link works after it was last used
	expired  = "This link has expired. Show the QR code again on the spoke."
)

type Shares struct {
	Recorder *recorder.Recorder
	Dir      string        // where phone copies go
	Name     func() string // the spoke's name, for the page

	mu       sync.Mutex
	links    map[string]link
	prepared map[string]*job  // session name -> its phone copy
	clips    map[string]*clip // clip id -> a trim being made or made
}

type link struct {
	name    string
	expires time.Time
}

type job struct {
	size  int64 // the recording's size when the copy was started
	state string
	err   string
}

type clip struct {
	name       string
	start, end float64
	state      string
	progress   float64
	err        string
}

func token(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return base64.RawURLEncoding.EncodeToString(b)
}

// kept is what survives a restart (a power blip, say), so a phone's link keeps working: the links,
// and which phone copies are made.
type kept struct {
	Links map[string]keptLink `json:"links"`
	Ready map[string]int64    `json:"ready"` // recording -> its size when its copy was made
}

type keptLink struct {
	Name    string    `json:"name"`
	Expires time.Time `json:"expires"`
}

func (s *Shares) keptPath() string { return filepath.Join(s.Dir, "links.json") }

// loadLocked starts from what was kept on the card, the first time it's needed.
func (s *Shares) loadLocked() {
	if s.links != nil {
		return
	}
	s.links, s.prepared, s.clips = map[string]link{}, map[string]*job{}, map[string]*clip{}
	var k kept
	data, err := os.ReadFile(s.keptPath())
	if err != nil || json.Unmarshal(data, &k) != nil {
		return
	}
	for t, l := range k.Links {
		if time.Now().Before(l.Expires) {
			s.links[t] = link{l.Name, l.Expires}
		}
	}
	for name, size := range k.Ready {
		if exists(s.copyPath(name)) {
			s.prepared[name] = &job{size: size, state: "ready"}
		}
	}
}

func (s *Shares) saveLocked() {
	k := kept{Links: map[string]keptLink{}, Ready: map[string]int64{}}
	for t, l := range s.links {
		k.Links[t] = keptLink{l.name, l.expires}
	}
	for name, j := range s.prepared {
		if j.state == "ready" {
			k.Ready[name] = j.size
		}
	}
	data, _ := json.Marshal(k)
	if os.MkdirAll(s.Dir, 0o755) == nil && os.WriteFile(s.keptPath()+".tmp", data, 0o600) == nil {
		_ = os.Rename(s.keptPath()+".tmp", s.keptPath())
	}
}

// Link makes a link for one recording and starts making its phone copy.
func (s *Shares) Link(name string) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.loadLocked()
	s.cleanLocked()
	t := token(12)
	s.links[t] = link{name, time.Now().Add(linkLife)}
	defer s.saveLocked()
	session, ok := s.Recorder.Find(name)
	j := s.prepared[name]
	if ok && (j == nil || j.size != session.Size || j.state == "failed") {
		// New, or a recording still in progress that has grown since
		j = &job{size: session.Size, state: "preparing"}
		s.prepared[name] = j
		go s.prepare(session, j)
	}
	return t
}

// State is how the phone copy of a recording is coming along: preparing, ready or failed.
func (s *Shares) State(name string) (string, string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.loadLocked()
	j := s.prepared[name]
	if j == nil {
		return "failed", ""
	}
	if j.state == "ready" && !exists(s.copyPath(name)) {
		j.state, j.err = "failed", "The phone copy was removed"
	}
	return j.state, j.err
}

func (s *Shares) copyPath(name string) string { return filepath.Join(s.Dir, name+".mp4") }

func (s *Shares) clipPath(id string, c *clip) string {
	return filepath.Join(s.Dir, c.name+".clip-"+id+".mp4")
}

func (s *Shares) fail(j *job, err string) {
	s.mu.Lock()
	j.state, j.err = "failed", err
	s.mu.Unlock()
}

func (s *Shares) prepare(session recorder.Session, j *job) {
	if err := os.MkdirAll(s.Dir, 0o755); err != nil {
		s.fail(j, err.Error())
		return
	}
	if _, err := exec.LookPath("ffmpeg"); err != nil {
		s.fail(j, "This spoke can't make phone copies yet (it needs ffmpeg)")
		return
	}
	if recorder.FreeBytes(s.Dir) < uint64(session.Size)+512<<20 {
		s.fail(j, "Not enough space on the card to prepare it")
		return
	}
	parts := make([]string, len(session.Parts))
	for i, p := range session.Parts {
		parts[i] = filepath.Join(session.Path, p)
	}
	target := s.copyPath(session.Name)
	partial := target + ".part"
	// aac_adtstoasc: MP4 stores AAC without the per-frame headers TS carries
	out, err := exec.Command("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "concat:"+strings.Join(parts, "|"),
		"-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", "-f", "mp4", partial).CombinedOutput()
	if err == nil {
		err = os.Rename(partial, target)
	}
	if err != nil {
		log.Printf("Preparing %s for a phone failed: %v %s", session.Name, err, strings.TrimSpace(string(out)))
		os.Remove(partial)
		s.fail(j, "Couldn't prepare this recording")
		return
	}
	s.mu.Lock()
	j.state = "ready"
	s.saveLocked()
	s.mu.Unlock()
}

// shared returns the recording a link is for, keeping the link alive while it's used.
func (s *Shares) shared(t string) (recorder.Session, bool) {
	s.mu.Lock()
	s.loadLocked()
	l, ok := s.links[t]
	if ok && time.Now().Before(l.expires) {
		renewed := time.Now().Add(linkLife)
		s.links[t] = link{l.name, renewed}
		if renewed.Sub(l.expires) > time.Minute { // (not on every request a page makes)
			s.saveLocked()
		}
	}
	s.mu.Unlock()
	if !ok || time.Now().After(l.expires) {
		return recorder.Session{}, false
	}
	return s.Recorder.Find(l.name)
}

// startClip begins copying start–end (seconds) of a shared recording into its own MP4.
func (s *Shares) startClip(session recorder.Session, start, end float64) (string, error) {
	if state, _ := s.State(session.Name); state != "ready" {
		return "", errors.New(expired)
	}
	start = max(0, start) // (past the end, the copy just stops there)
	if keys := recorder.Keyframes(session); len(keys) > 0 {
		// The keyframe at (or just before) the start: the clip begins exactly on it
		at := keys[0]
		for _, k := range keys {
			if k <= start+0.05 {
				at = k
			}
		}
		start = at
	}
	if end-start < 1 {
		return "", errors.New("The clip needs to be at least a second long")
	}
	share := min(1, (end-start)/max(session.Duration.Seconds(), 1))
	if recorder.FreeBytes(s.Dir) < uint64(float64(session.Size)*share)+256<<20 {
		return "", errors.New("Not enough space on the card for this clip")
	}
	id := token(6)
	c := &clip{name: session.Name, start: start, end: end, state: "preparing"}
	s.mu.Lock()
	s.clips[id] = c
	s.mu.Unlock()
	go s.cut(id, c)
	return id, nil
}

func (s *Shares) cut(id string, c *clip) {
	target := s.clipPath(id, c)
	partial := target + ".part"
	// Seeking before the input jumps to the keyframe at or before the start, and the copy runs from there
	cmd := exec.Command("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-nostats", "-progress", "pipe:1",
		// A little past the keyframe, so it's never the one before that ffmpeg starts on: the phone copy's
		// timeline can sit a frame or two later than the recording's (B-frames delay each picture)
		"-ss", fmt.Sprintf("%.3f", c.start+0.25), "-to", fmt.Sprintf("%.3f", c.end), "-i", s.copyPath(c.name),
		"-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-avoid_negative_ts", "make_zero",
		"-movflags", "+faststart", "-f", "mp4", partial)
	stdout, _ := cmd.StdoutPipe()
	var stderr strings.Builder
	cmd.Stderr = &stderr
	err := cmd.Start()
	if err == nil {
		lines := bufio.NewScanner(stdout)
		for lines.Scan() {
			if us, ok := strings.CutPrefix(lines.Text(), "out_time_us="); ok {
				if v, err := strconv.ParseFloat(us, 64); err == nil {
					s.mu.Lock()
					c.progress = min(0.99, v/1e6/(c.end-c.start))
					s.mu.Unlock()
				}
			}
		}
		err = cmd.Wait()
	}
	if err == nil {
		err = os.Rename(partial, target)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if err != nil {
		log.Printf("Clip of %s failed: %v %s", c.name, err, strings.TrimSpace(stderr.String()))
		os.Remove(partial)
		c.state, c.err = "failed", "Couldn't make the clip"
		return
	}
	c.state, c.progress = "ready", 1
}

// cleanLocked forgets expired links and deletes copies no live link points at. A phone still
// downloading one keeps its open file, so this never cuts a download short.
func (s *Shares) cleanLocked() {
	live := map[string]bool{}
	for t, l := range s.links {
		if time.Now().After(l.expires) {
			delete(s.links, t)
		} else {
			live[l.name] = true
		}
	}
	files, _ := os.ReadDir(s.Dir)
	for _, f := range files {
		name := strings.SplitN(f.Name(), ".", 2)[0]
		if j := s.prepared[name]; !live[name] && (j == nil || j.state != "preparing") {
			os.Remove(filepath.Join(s.Dir, f.Name()))
			delete(s.prepared, name)
		}
	}
	for id, c := range s.clips {
		if !live[c.name] && c.state != "preparing" {
			delete(s.clips, id)
		}
	}
}

// Routes serves /r/<link> (the page) and what it uses, /download/<link>, and /brand/ for its
// fonts and mark.
func (s *Shares) Routes(mux *http.ServeMux) {
	mux.HandleFunc("GET /brand/{file}", func(w http.ResponseWriter, r *http.Request) {
		name := r.PathValue("file")
		if name == "touchstream_mark.png" {
			name = "mark.png"
		}
		if !strings.HasSuffix(name, ".ttf") && name != "mark.png" {
			http.NotFound(w, r)
			return
		}
		data, err := assets.Read(name)
		if err != nil {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Cache-Control", "max-age=86400")
		http.ServeContent(w, r, name, time.Time{}, strings.NewReader(string(data)))
	})
	mux.HandleFunc("GET /r/{token}", func(w http.ResponseWriter, r *http.Request) {
		page, _ := assets.Read("recording.html")
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write(page)
	})
	mux.HandleFunc("GET /r/{token}/info", s.with(func(w http.ResponseWriter, r *http.Request, rec recorder.Session) {
		state, err := s.State(rec.Name)
		reply(w, http.StatusOK, map[string]any{
			"title": rec.Start.Format("Monday Jan 2 · 3:04 PM"), "where": s.Name(),
			"duration": rec.Duration.Seconds(), "size": rec.Size, "state": state, "error": err,
		})
	}))
	mux.HandleFunc("GET /r/{token}/keyframes", s.with(func(w http.ResponseWriter, r *http.Request, rec recorder.Session) {
		keys := recorder.Keyframes(rec)
		if keys == nil {
			keys = []float64{}
		}
		reply(w, http.StatusOK, map[string]any{"keyframes": keys})
	}))
	mux.HandleFunc("GET /r/{token}/video.mp4", s.with(func(w http.ResponseWriter, r *http.Request, rec recorder.Session) {
		s.serveFile(w, r, rec.Name, s.copyPath(rec.Name), "")
	}))
	mux.HandleFunc("GET /download/{token}", s.with(func(w http.ResponseWriter, r *http.Request, rec recorder.Session) {
		// (".mp4" goes on after: in a Go time layout its 4 would be the minute)
		s.serveFile(w, r, rec.Name, s.copyPath(rec.Name), rec.Start.Format("TouchStream 2006-01-02 15.04")+".mp4")
	}))
	mux.HandleFunc("POST /r/{token}/clip", s.with(func(w http.ResponseWriter, r *http.Request, rec recorder.Session) {
		var body struct{ Start, End *float64 }
		if json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&body) != nil || body.Start == nil || body.End == nil {
			reply(w, http.StatusBadRequest, map[string]any{"error": "Pick a start and an end"})
			return
		}
		id, err := s.startClip(rec, *body.Start, *body.End)
		if err != nil {
			reply(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		reply(w, http.StatusOK, map[string]any{"id": id})
	}))
	mux.HandleFunc("GET /r/{token}/clip/{id}", s.with(func(w http.ResponseWriter, r *http.Request, rec recorder.Session) {
		c, path, ok := s.clipFor(rec, r.PathValue("id"))
		if !ok {
			reply(w, http.StatusNotFound, map[string]any{"error": expired})
			return
		}
		var size any
		if info, err := os.Stat(path); err == nil && c.state == "ready" {
			size = info.Size()
		}
		reply(w, http.StatusOK, map[string]any{"state": c.state, "progress": c.progress, "error": nilIfEmpty(c.err),
			"start": c.start, "end": c.end, "size": size})
	}))
	mux.HandleFunc("GET /r/{token}/clip/{id}/file", s.with(func(w http.ResponseWriter, r *http.Request, rec recorder.Session) {
		c, path, ok := s.clipFor(rec, r.PathValue("id"))
		if !ok || c.state != "ready" {
			text(w, http.StatusNotFound, expired)
			return
		}
		at := rec.Start.Add(time.Duration(c.start * float64(time.Second)))
		http.ServeFile(withName(w, at.Format("TouchStream 2006-01-02 15.04.05")+" clip.mp4"), r, path)
	}))
}

// with looks up the recording a link is for, or answers that the link has expired: in JSON for
// the page's own requests, as text for the files a browser opens.
func (s *Shares) with(h func(http.ResponseWriter, *http.Request, recorder.Session)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rec, ok := s.shared(r.PathValue("token"))
		switch {
		case !ok && isFile(r):
			text(w, http.StatusNotFound, expired)
		case !ok:
			reply(w, http.StatusNotFound, map[string]any{"error": expired})
		default:
			h(w, r, rec)
		}
	}
}

func isFile(r *http.Request) bool {
	return strings.HasSuffix(r.URL.Path, "/file") || strings.HasSuffix(r.URL.Path, ".mp4") ||
		strings.HasPrefix(r.URL.Path, "/download/")
}

func (s *Shares) clipFor(rec recorder.Session, id string) (clip, string, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	c := s.clips[id]
	if c == nil || c.name != rec.Name {
		return clip{}, "", false
	}
	return *c, s.clipPath(id, c), true
}

// serveFile sends the phone copy, to play in the page, or as an attachment (so phones save it)
// when name is given. Ranges let a player seek and an interrupted download resume.
func (s *Shares) serveFile(w http.ResponseWriter, r *http.Request, recording, path, name string) {
	if state, _ := s.State(recording); state != "ready" {
		text(w, http.StatusNotFound, expired)
		return
	}
	if name != "" {
		w = withName(w, name)
	}
	http.ServeFile(w, r, path)
}

func withName(w http.ResponseWriter, name string) http.ResponseWriter {
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%q", name))
	return w
}

func reply(w http.ResponseWriter, code int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(body)
}

func text(w http.ResponseWriter, code int, msg string) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(code)
	_, _ = w.Write([]byte(msg))
}

func nilIfEmpty(v string) any {
	if v == "" {
		return nil
	}
	return v
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}
