// Package spoke ties the parts together: it does what the hub asks (stream, record) and
// reports back in the same terms as the Pi spoke, so the hub dashboard shows both alike.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package spoke

import (
	"fmt"
	"log"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/Cinderwire-co/TouchStream/lite/internal/config"
	"github.com/Cinderwire-co/TouchStream/lite/internal/hub"
	"github.com/Cinderwire-co/TouchStream/lite/internal/media"
	"github.com/Cinderwire-co/TouchStream/lite/internal/recorder"
	"github.com/Cinderwire-co/TouchStream/lite/internal/uplink"
)

const Version = "0.1.0-lite"

type Spoke struct {
	Store    *config.Store
	Source   media.Source
	Uplink   *uplink.Uplink
	Recorder *recorder.Recorder
	Uploader *recorder.Uploader
	Link     *hub.Link

	mu         sync.Mutex
	mode       string    // off, preview or live, as the hub asked
	manual     time.Time // when Record was pressed on the device, while recording that way
	recordedAt time.Time // when the current recording started
	cpu        cpuSampler
}

// Apply makes the spoke match what the hub asked for (nil: unpaired, so stop everything
// except a recording started on the device).
func (s *Spoke) Apply(d *hub.Desired) {
	if d == nil {
		d = &hub.Desired{Mode: "off"}
	}
	if d.Name != "" {
		c := s.Store.Get()
		if d.Name != c.DeviceName || d.Location != c.Location {
			_ = s.Store.Update(func(c *config.Config) { c.DeviceName, c.Location = d.Name, d.Location })
		}
	}
	s.mu.Lock()
	s.mode = d.Mode
	manual := !s.manual.IsZero()
	s.mu.Unlock()

	if d.Mode != "off" && d.Stream != nil {
		st := d.Stream
		s.Uplink.Set(&uplink.Target{Host: st.Host, Port: st.Port, StreamID: st.StreamID, LatencyMS: st.LatencyMS})
	} else {
		s.Uplink.Set(nil)
	}
	s.setRecording(manual || (d.RecordLocal && d.Mode == "live"))
	for _, command := range d.Commands {
		log.Printf("Hub command %q: not on this spoke yet", command)
	}
}

// ToggleRecord is the device's own Record button: record here, with or without a hub. A
// recording started here carries on whatever the hub asks, until it's stopped here.
func (s *Spoke) ToggleRecord() bool {
	s.mu.Lock()
	if s.manual.IsZero() {
		s.manual = time.Now()
	} else {
		s.manual = time.Time{}
	}
	on, live := !s.manual.IsZero(), s.mode == "live"
	s.mu.Unlock()
	s.setRecording(on || live)
	return on
}

func (s *Spoke) setRecording(on bool) {
	if on == s.Recorder.Recording() {
		return
	}
	if on {
		if _, err := s.Recorder.Start(); err != nil {
			log.Printf("Recording: %v", err)
			return
		}
		s.mu.Lock()
		s.recordedAt = time.Now()
		s.mu.Unlock()
	} else {
		s.Recorder.Stop()
	}
}

// State is a snapshot for the device's screen.
type State struct {
	Name       string
	Mode       string
	Paired     bool
	HubOK      bool
	Signal     media.Signal
	Uplink     string
	Recording  bool
	RecordedAt time.Time
	Manual     bool
	IP         string
	Uploading  string
}

func (s *Spoke) State(ip string) State {
	c := s.Store.Get()
	ok, _ := s.Link.Connected()
	up, _ := s.Uplink.State()
	s.mu.Lock()
	defer s.mu.Unlock()
	return State{
		Name: c.DeviceName, Mode: s.mode, Paired: c.Paired(), HubOK: ok, Signal: s.Source.Signal(),
		Uplink: up, Recording: s.Recorder.Recording(), RecordedAt: s.recordedAt, Manual: !s.manual.IsZero(),
		IP: ip, Uploading: s.Uploader.Status(),
	}
}

// Status is what the heartbeat reports to the hub.
func (s *Spoke) Status() map[string]any {
	sig := s.Source.Signal()
	up, upErr := s.Uplink.State()
	s.mu.Lock()
	mode := s.mode
	s.mu.Unlock()
	status := map[string]any{
		"version": Version, "model": "picokvm", "signal": sig.Present,
		"signal_label": nil, "signal_fps": nil, "framerate": nonEmpty(s.Store.Get().Framerate, "30"),
		"source": nil, "test_pattern": false, "test_pattern_at": 0,
		"temp": cpuTemp(), "cpu": s.cpu.percent(), "mode": nonEmpty(mode, "off"), "network": "Ethernet",
		"uplink": up, "uplink_error": nilIfEmpty(upErr),
		"recording": s.Recorder.Recording(), "uploading": nilIfEmpty(s.Uploader.Status()),
		"direct": false, "direct_state": nil, "storage": s.Uploader.Storage(), "speed": nil,
		"memory_mb": memoryMB(),
	}
	if sig.Present {
		label := strconv.FormatFloat(sig.FPS, 'f', 2, 64)
		label = strings.TrimRight(strings.TrimRight(label, "0"), ".")
		status["signal_label"] = fmt.Sprintf("%dp%s", sig.Height, label)
		status["signal_fps"] = sig.FPS
		status["source"] = "hdmi"
	}
	return status
}

func nonEmpty(v, fallback string) string {
	if v == "" {
		return fallback
	}
	return v
}

func nilIfEmpty(v string) any {
	if v == "" {
		return nil
	}
	return v
}

// cpuTemp is the chip's temperature in °C, where the system reports one.
func cpuTemp() any {
	data, err := os.ReadFile("/sys/class/thermal/thermal_zone0/temp")
	if err != nil {
		return nil
	}
	milli, err := strconv.Atoi(strings.TrimSpace(string(data)))
	if err != nil {
		return nil
	}
	return float64(milli) / 1000
}

// memoryMB is how much of the PicoKVM's 256 MB this process holds (resident).
func memoryMB() any {
	data, err := os.ReadFile("/proc/self/statm")
	if err != nil {
		var m runtime.MemStats
		runtime.ReadMemStats(&m)
		return m.Sys >> 20
	}
	fields := strings.Fields(string(data))
	if len(fields) < 2 {
		return nil
	}
	pages, _ := strconv.Atoi(fields[1])
	return pages * os.Getpagesize() >> 20
}

// cpuSampler works out CPU use from /proc/stat between two heartbeats.
type cpuSampler struct {
	mu          sync.Mutex
	busy, total uint64
}

func (c *cpuSampler) percent() any {
	data, err := os.ReadFile("/proc/stat")
	if err != nil {
		return nil
	}
	line := strings.SplitN(string(data), "\n", 2)[0]
	var busy, total uint64
	for i, f := range strings.Fields(line)[1:] {
		v, _ := strconv.ParseUint(f, 10, 64)
		total += v
		if i != 3 && i != 4 { // idle, iowait
			busy += v
		}
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	prevBusy, prevTotal := c.busy, c.total
	c.busy, c.total = busy, total
	if prevTotal == 0 || total == prevTotal {
		return nil // the first sample has nothing to compare with
	}
	return float64(busy-prevBusy) * 100 / float64(total-prevTotal)
}
