package recorder

import (
	"context"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"time"

	"golang.org/x/sys/unix"

	"github.com/Cinderwire-co/TouchStream/lite/internal/config"
	"github.com/Cinderwire-co/TouchStream/lite/internal/hub"
)

const (
	uploadEvery  = 10 * time.Second
	MinFreeBytes = 2 << 30 // keep 2 GB free on the card (about 25 minutes at 12 Mbps)
)

// Uploader sends finished parts to the hub while the recording carries on, so little is
// left to send when it ends, and makes room on the card when it runs low.
type Uploader struct {
	Recorder *Recorder
	Store    *config.Store

	mu     sync.Mutex
	status string // e.g. "2 of 5" while uploading, for the hub dashboard
}

func (u *Uploader) Status() string {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.status
}

func (u *Uploader) setStatus(s string) {
	u.mu.Lock()
	u.status = s
	u.mu.Unlock()
}

func (u *Uploader) Run(ctx context.Context) {
	for {
		if c := u.Store.Get(); c.Paired() {
			for _, s := range u.Recorder.Sessions() {
				if err := u.upload(ctx, c, s); err != nil {
					log.Printf("Uploading %s: %v", s.Name, err)
					break
				}
			}
		}
		u.setStatus("")
		u.prune()
		select {
		case <-ctx.Done():
			return
		case <-time.After(uploadEvery):
		}
	}
}

func (u *Uploader) upload(ctx context.Context, c config.Config, s Session) error {
	if s.OnHub {
		return nil
	}
	parts := s.Parts
	if !s.Complete && len(parts) > 0 {
		parts = parts[:len(parts)-1] // the newest part is still being written
	}
	var todo []string
	for _, p := range parts {
		if !s.Uploaded[p] {
			todo = append(todo, p)
		}
	}
	// Session names are local time; send the exact moment so the hub can match this copy to its
	// broadcast whatever its own time zone
	start, _ := time.ParseInLocation(SessionLayout, s.Name, time.Local)
	for i, name := range todo {
		u.setStatus(fmt.Sprintf("%d of %d", len(s.Uploaded)+1, len(s.Uploaded)+len(todo)-i))
		if err := u.put(ctx, c, s, name, start); err != nil {
			return err
		}
		s.Uploaded[name] = true
		f, err := os.OpenFile(filepath.Join(s.Path, ".uploaded"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		if err != nil {
			return err
		}
		fmt.Fprintln(f, name)
		f.Close()
	}
	if !s.Complete || len(s.Uploaded) < len(s.Parts) {
		return nil
	}
	ended := start
	if info, err := os.Stat(filepath.Join(s.Path, ".complete")); err == nil {
		ended = info.ModTime()
	}
	times := map[string]float64{"started_at": unixSeconds(start), "ended_at": unixSeconds(ended)}
	url := fmt.Sprintf("%s/api/spoke/recordings/%s/complete", c.HubURL, s.Name)
	if err := hub.Request(ctx, "POST", url, times, hub.Auth(c), nil); err != nil {
		return err
	}
	log.Printf("Recording %s uploaded to the hub", s.Name)
	return os.WriteFile(filepath.Join(s.Path, ".merged"), nil, 0o644)
}

func (u *Uploader) put(ctx context.Context, c config.Config, s Session, name string, start time.Time) error {
	f, err := os.Open(filepath.Join(s.Path, name))
	if err != nil {
		return err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Minute) // a 5-minute part over 100 Mbit takes about 20 s
	defer cancel()
	headers := hub.Auth(c)
	headers["Content-Type"] = "video/mp2t"
	headers["X-Session-Start"] = strconv.FormatFloat(unixSeconds(start), 'f', 0, 64)
	url := fmt.Sprintf("%s/api/spoke/recordings/%s/%s", c.HubURL, s.Name, name)
	return hub.Request(ctx, "PUT", url, sizedReader{f, info.Size()}, headers, nil)
}

// sizedReader tells the request its length: the hub checks the whole part arrived.
type sizedReader struct {
	*os.File
	size int64
}

func (r sizedReader) Size() int64 { return r.size }

func unixSeconds(t time.Time) float64 { return float64(t.UnixNano()) / 1e9 }

// prune deletes the oldest finished recordings while the card is low: ones the hub already
// has first, then ones only here. The recording in progress is never touched.
func (u *Uploader) prune() {
	sessions := u.Recorder.Sessions()
	sort.SliceStable(sessions, func(i, j int) bool { return sessions[i].OnHub && !sessions[j].OnHub })
	for _, s := range sessions {
		if FreeBytes(u.Recorder.Dir) >= MinFreeBytes {
			return
		}
		if s.Recording {
			continue
		}
		note := ""
		if !s.OnHub {
			note = " (not on the hub)"
		}
		log.Printf("Low on space: deleting recording %s%s", s.Name, note)
		os.RemoveAll(s.Path)
	}
}

// Storage is the card's space and the recordings on it, for the hub dashboard.
func (u *Uploader) Storage() map[string]any {
	var st unix.Statfs_t
	_ = os.MkdirAll(u.Recorder.Dir, 0o755)
	_ = unix.Statfs(u.Recorder.Dir, &st)
	sessions := u.Recorder.Sessions()
	var size int64
	onlyHere := 0
	for _, s := range sessions {
		size += s.Size
		if !s.OnHub && !s.Recording {
			onlyHere++
		}
	}
	return map[string]any{
		"free": st.Bavail * uint64(st.Bsize), "total": st.Blocks * uint64(st.Bsize),
		"recordings": len(sessions), "recordings_bytes": size, "only_here": onlyHere,
	}
}

func FreeBytes(dir string) uint64 {
	var st unix.Statfs_t
	_ = os.MkdirAll(dir, 0o755)
	if unix.Statfs(dir, &st) != nil {
		return 0
	}
	return st.Bavail * uint64(st.Bsize)
}
