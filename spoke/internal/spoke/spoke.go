// Package spoke ties the parts together: it does what the hub asks (stream, record) and
// reports back what it's doing, for the hub dashboard.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package spoke

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/clock"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/config"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/direct"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/hub"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/media"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/recorder"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/uplink"
)

const Version = "0.1.0-dev"

// Rates is the 1080p frame rates the PicoKVM can capture: the hub offers only these.
var Rates = []string{"24", "25", "30", "50", "60"}

type Spoke struct {
	Store    *config.Store
	Source   media.Source
	Uplink   *uplink.Uplink
	Recorder *recorder.Recorder
	Uploader *recorder.Uploader
	Link     *hub.Link
	Direct   *direct.Stream // straight to YouTube: with no hub, or when it's out of reach
	Data     string         // where the hub's plan is kept for then

	mu         sync.Mutex
	mode       string    // off, preview or live, as the hub asked (or as Go live did, with no hub)
	ownLive    bool      // live on the spoke's own: hubless, or carried on without the hub
	ownEvent   int       // the scheduled broadcast it's running without the hub (0: none)
	liveSince  time.Time // when it went live
	pending    string    // a mode asked for on the device, until the hub confirms it
	asked      time.Time
	problem    string // why the last thing asked on the device didn't happen, for a few seconds
	problemAt  time.Time
	manual     time.Time // when Record was pressed on the device, while recording that way
	recordedAt time.Time // when the current recording started
	kept       []int     // the broadcasts in the schedule kept on the card, for the hub to show (nil: none yet)
	cpu        cpuSampler
}

const (
	pendingFor = 10 * time.Second // after this the hub's answer, whatever it is, stands
	problemFor = 6 * time.Second
)

// FindZone looks up where the box is on the internet, for its time zone: until it's found, every
// minute, then once a day (it can be moved). The hub's choice comes first, if it's made one.
func (s *Spoke) FindZone(ctx context.Context) {
	s.useZone()
	for {
		wait := time.Minute
		if zone, err := clock.Lookup(ctx); err == nil {
			if zone != s.Store.Get().FoundZone {
				_ = s.Store.Update(func(c *config.Config) { c.FoundZone = zone })
				log.Printf("Its time zone, from where it is: %s", zone)
				s.useZone()
			}
			wait = 24 * time.Hour
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
		}
	}
}

// useZone makes the spoke's time its time zone's.
func (s *Spoke) useZone() {
	if zone := s.Store.Get().Zone(); zone != "" && zone != time.Local.String() {
		if err := clock.Zone(zone); err != nil {
			log.Printf("Time zone %s: %v", zone, err)
		}
	}
}

// ErrNoKeys: Go live with no hub needs a stream key, added on the setup page.
var ErrNoKeys = errors.New("add a stream key first")

// Apply makes the spoke match what the hub asked for (nil: unpaired, so stop everything
// except a recording started on the device).
func (s *Spoke) Apply(d *hub.Desired) {
	if d == nil {
		d = &hub.Desired{Mode: "off"}
	} else if err := direct.Save(s.Data, d.Plan); err != nil {
		log.Printf("Keeping the schedule: %v", err)
	} else {
		kept := make([]int, len(d.Plan.Schedule))
		for i, e := range d.Plan.Schedule {
			kept[i] = e.ID
		}
		s.mu.Lock()
		s.kept = kept
		s.mu.Unlock()
	}
	if s.Direct.Active() {
		log.Print("The hub is back; it takes over from the direct stream")
		s.Direct.Set(nil)
	}
	s.mu.Lock()
	s.ownLive, s.ownEvent = false, 0 // the hub has it again
	s.mu.Unlock()
	if d.Name != "" {
		c := s.Store.Get()
		if d.Name != c.DeviceName || d.Location != c.Location {
			_ = s.Store.Update(func(c *config.Config) { c.DeviceName, c.Location = d.Name, d.Location })
		}
	}
	if c := s.Store.Get(); d.TimeZone != c.TimeZone && (d.TimeZone == "" || clock.Valid(d.TimeZone)) {
		_ = s.Store.Update(func(c *config.Config) { c.TimeZone = d.TimeZone })
		s.useZone()
	}
	if d.Framerate != "" && d.Framerate != s.Store.Get().Framerate {
		// On the box this also tells the HDMI source which rate to send (still to come)
		_ = s.Store.Update(func(c *config.Config) { c.Framerate = d.Framerate })
		log.Printf("Frame rate set to 1080p%s", d.Framerate)
	}
	s.mu.Lock()
	if d.Mode == "live" && s.mode != "live" {
		s.liveSince = time.Now()
	}
	s.mode = d.Mode
	if s.pending == d.Mode || time.Since(s.asked) > pendingFor {
		s.pending = ""
	}
	manual := !s.manual.IsZero()
	s.mu.Unlock()

	if d.Mode != "off" && d.Stream != nil {
		st := d.Stream
		s.Uplink.Set(&uplink.Target{Host: st.Host, Port: st.Port, StreamID: st.StreamID, LatencyMS: st.LatencyMS})
	} else {
		s.Uplink.Set(nil)
	}
	s.setRecording(manual || d.Mode == "live") // streaming always keeps a copy on the card
	for _, command := range d.Commands {
		log.Printf("Hub command %q: not on this spoke yet", command)
	}
}

// ToggleRecord is the device's own Record button: record here, with or without a hub. A
// recording started here carries on whatever the hub asks, until it's stopped here. (Live, it
// records anyway.)
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

// GoLive is the device's own Go live and End. With a hub it asks the hub, which starts or ends
// the broadcast as if from its dashboard, and the next heartbeat carries it out. With no hub it
// streams straight to the stream keys added on the setup page.
func (s *Spoke) GoLive(on bool) error {
	mode := map[bool]string{true: "live", false: "off"}[on]
	c := s.Store.Get()
	s.mu.Lock()
	own := s.ownLive
	s.mu.Unlock()
	if !c.Paired() || (own && !on) {
		return s.goLiveAlone(on, c)
	}
	s.mu.Lock()
	s.pending, s.asked = mode, time.Now()
	s.mu.Unlock()
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
		defer cancel()
		err := hub.Request(ctx, "POST", c.HubURL+"/api/spoke/mode", map[string]string{"mode": mode}, hub.Auth(c), nil)
		if err != nil {
			log.Printf("Asking the hub to go %s: %v", mode, err)
			s.mu.Lock()
			s.pending = ""
			s.mu.Unlock()
			s.fail("Can't reach the hub")
		}
	}()
	return nil
}

func (s *Spoke) goLiveAlone(on bool, c config.Config) error {
	if !on {
		s.Direct.Set(nil)
		s.mu.Lock()
		s.mode, s.ownLive, s.ownEvent = "off", false, 0
		manual := !s.manual.IsZero()
		s.mu.Unlock()
		s.setRecording(manual)
		log.Print("Ended the direct stream")
		return nil
	}
	if len(c.Destinations) == 0 {
		return ErrNoKeys
	}
	targets := make([]direct.Target, len(c.Destinations))
	for i, d := range c.Destinations {
		targets[i] = direct.Target{URL: d.URL, Key: d.Key, Name: d.Name}
	}
	s.startOwn(targets)
	return nil
}

// startOwn goes live straight to YouTube, keeping a copy on the card as it always does live.
func (s *Spoke) startOwn(targets []direct.Target) {
	s.mu.Lock()
	if s.mode != "live" {
		s.liveSince = time.Now()
	}
	s.mode, s.ownLive = "live", true
	s.mu.Unlock()
	s.Uplink.Set(nil)
	s.Direct.Set(targets)
	s.setRecording(true)
}

// Watch runs the spoke's own copy of its schedule while the hub is out of reach.
func (s *Spoke) Watch(ctx context.Context) {
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-tick.C:
			s.carryOn(now)
		}
	}
}

// carryOn runs the spoke's own copy of its schedule while the hub is out of reach: a broadcast
// that's due goes straight to YouTube if it has channels, and is recorded here either way (so
// with no internet at all, it's still recorded); it stops at the broadcast's end time. Live when
// the hub went away, it carries on with the hub's stream keys until it's ended here or the hub's back.
func (s *Spoke) carryOn(now time.Time) {
	if !s.Store.Get().Paired() || now.Sub(s.Link.LastOK()) < direct.HubTimeout {
		return
	}
	plan := direct.Load(s.Data)
	event := plan.Due(now)
	s.mu.Lock()
	live, own, mine := s.mode == "live", s.ownLive, s.ownEvent
	s.mu.Unlock()
	switch {
	case event != nil && (!own || mine != event.ID):
		log.Printf("Hub out of reach: running %q here (%d channel(s))", event.Title, len(event.Targets))
		s.startOwn(append(append([]direct.Target{}, event.Targets...), plan.Destinations...))
		s.mu.Lock()
		s.ownEvent = event.ID
		s.mu.Unlock()
	case event == nil && live && !own:
		log.Print("Hub out of reach: carrying on live, straight to YouTube")
		s.startOwn(plan.Destinations)
	case event == nil && own && mine != 0:
		log.Print("Scheduled broadcast over; stopping")
		_ = s.goLiveAlone(false, s.Store.Get())
	}
}

func (s *Spoke) fail(why string) {
	s.mu.Lock()
	s.problem, s.problemAt = why, time.Now()
	s.mu.Unlock()
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
	LiveSince  time.Time
	Pending    string // live or off: asked for on the device, waiting for the hub
	Problem    string // why the last thing asked on the device didn't happen
	Direct     bool   // streaming straight to YouTube
	DirectNow  string // connecting, sending or retrying, while it is
	HubNow     string // connecting, connected or unreachable
	Keys       int    // stream keys of its own, for Go live with no hub
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
	problem := s.problem
	if time.Since(s.problemAt) > problemFor {
		problem = ""
	}
	return State{
		Name: c.DeviceName, Mode: s.mode, Paired: c.Paired(), HubOK: ok, Signal: s.Source.Signal(),
		Uplink: up, LiveSince: s.liveSince, Pending: s.pending, Problem: problem,
		Direct: s.Direct.Active(), DirectNow: s.Direct.State(), HubNow: s.Link.State(), Keys: len(c.Destinations),
		Recording: s.Recorder.Recording(), RecordedAt: s.recordedAt, Manual: !s.manual.IsZero(),
		IP: ip, Uploading: s.Uploader.Status(),
	}
}

// Status is what the heartbeat reports to the hub.
func (s *Spoke) Status() map[string]any {
	sig := s.Source.Signal()
	up, upErr := s.Uplink.State()
	s.mu.Lock()
	mode, kept := s.mode, s.kept
	s.mu.Unlock()
	status := map[string]any{
		"schedule_kept": kept,
		"version":       Version, "model": "picokvm", "framerates": Rates, "signal": sig.Present,
		"signal_label": nil, "signal_fps": nil, "stream_fps": nil, "framerate": nonEmpty(s.Store.Get().Framerate, "30"),
		"source": nil, "test_pattern": false, "test_pattern_at": 0,
		"temp": cpuTemp(), "cpu": s.cpu.percent(), "mode": nonEmpty(mode, "off"), "network": "Ethernet",
		"uplink": up, "uplink_error": nilIfEmpty(upErr),
		"recording": s.Recorder.Recording(), "uploading": nilIfEmpty(s.Uploader.Status()),
		"direct": s.Direct.Active(), "direct_state": s.Direct.State(), "storage": s.Uploader.Storage(), "speed": nil,
		"memory_mb": memoryMB(),
		"time_zone": nilIfEmpty(s.Store.Get().Zone()), "time_zone_found": nilIfEmpty(s.Store.Get().FoundZone),
		"clock_set": clock.Set(),
	}
	if sig.Present {
		label := strconv.FormatFloat(sig.FPS, 'f', 2, 64)
		label = strings.TrimRight(strings.TrimRight(label, "0"), ".")
		status["signal_label"] = fmt.Sprintf("%dp%s", sig.Height, label)
		status["signal_fps"] = sig.FPS
		status["stream_fps"] = sig.StreamRate()
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
