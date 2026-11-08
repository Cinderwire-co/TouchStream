package media

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/ts"
)

// Capture is the box's HDMI input. Its helper (touchstream-capture, in C, with the box's encoder)
// turns the picture into H.264 on its stdout, with the stream's parameters before every keyframe
// and no B-frames (as the RV1106 makes it: so each frame's time is its place in the stream), and
// says what's coming in on its stderr, a JSON line each time it changes: the source's rate, and
// the stream's (every 2nd frame, when the source sends twice the rate the spoke's set to):
//
//	{"signal": true, "width": 1920, "height": 1080, "fps": 59.94, "rate": 29.97}
//	{"signal": false}
//
// For each signal it locks onto, ffmpeg (the box's own) adds the HDMI sound as AAC and makes
// MPEG-TS of the two. Its clocks carry on from the last one's, so the stream never runs
// backwards, however often the cable comes and goes.
type Capture struct {
	Helper   []string                      // the helper's command; it's given -fps and -bitrate too
	Audio    []string                      // ffmpeg's input for the sound: -f alsa -i hw:..., say
	Settings func() (fps string, kbps int) // what the hub (or the setup page) asks for

	signal atomic.Pointer[Signal]
}

type status struct {
	Signal bool    `json:"signal"`
	Width  int     `json:"width"`
	Height int     `json:"height"`
	FPS    float64 `json:"fps"`
	Rate   float64 `json:"rate"` // (an older helper doesn't say: the source's)
}

func (c *Capture) Signal() Signal {
	if s := c.signal.Load(); s != nil {
		return *s
	}
	return Signal{}
}

func (c *Capture) Run(ctx context.Context, emit func([]byte)) error {
	var clock carry
	for ctx.Err() == nil {
		fps, kbps := c.Settings()
		err := c.session(ctx, fps, kbps, &clock, emit)
		c.signal.Store(nil)
		if err != nil && ctx.Err() == nil {
			log.Printf("HDMI capture: %v", err)
		}
		select {
		case <-ctx.Done():
		case <-time.After(2 * time.Second):
		}
	}
	return ctx.Err()
}

// session runs the helper until it stops, or the frame rate or bitrate asked for changes (the
// frame rate is in the EDID it gives the source, so it starts again).
func (c *Capture) session(ctx context.Context, fps string, kbps int, clock *carry, emit func([]byte)) error {
	ctx, stop := context.WithCancel(ctx)
	defer stop()
	args := append(append([]string{}, c.Helper[1:]...), "-fps", fps, "-bitrate", strconv.Itoa(kbps))
	helper := exec.CommandContext(ctx, c.Helper[0], args...)
	video, err := helper.StdoutPipe()
	if err != nil {
		return err
	}
	notes, err := helper.StderrPipe()
	if err != nil {
		return err
	}
	if err := helper.Start(); err != nil {
		return err
	}

	// The picture goes to whichever ffmpeg is making the stream (none while there's no signal), as
	// it comes (ffmpeg takes the time it comes as its time: see MuxArgs), from a keyframe's
	// parameters (SPS) on: before them it couldn't make sense of it
	var mu sync.Mutex
	var current io.Writer
	var fresh bool // the current ffmpeg hasn't had a keyframe yet
	go func() {
		buf := make([]byte, 64<<10)
		for {
			n, err := video.Read(buf)
			mu.Lock()
			if b := buf[:n]; len(b) > 0 && current != nil {
				if fresh {
					if at := spsAt(b); at >= 0 {
						b, fresh = b[at:], false
					} else {
						b = nil
					}
				}
				if len(b) > 0 {
					_, _ = current.Write(b) // a failed write is an ffmpeg that's ending
				}
			}
			mu.Unlock()
			if err != nil {
				stop()
				return
			}
		}
	}()
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
				if f, k := c.Settings(); f != fps || k != kbps {
					log.Printf("HDMI capture: now 1080p%s at %d kbps", f, k)
					stop()
					return
				}
			}
		}
	}()

	statuses := make(chan status)
	go func() {
		defer close(statuses)
		lines := bufio.NewScanner(notes)
		for lines.Scan() {
			var st status
			if json.Unmarshal(lines.Bytes(), &st) != nil {
				log.Printf("HDMI capture: %s", lines.Text())
				continue
			}
			select {
			case statuses <- st:
			case <-ctx.Done():
				return
			}
		}
	}()

	// An ffmpeg for each signal; one that stops while the signal's still there starts again
	var now *status // the signal coming in, if there is one
	var mux *muxer
	var again <-chan time.Time
	begin := func() {
		m, err := c.mux(ctx, Signal{FPS: now.FPS, Rate: now.Rate}.StreamRate(), clock, emit)
		if err != nil {
			log.Printf("HDMI capture: ffmpeg: %v", err)
			again = time.After(2 * time.Second)
			return
		}
		mux = m
		mu.Lock()
		current, fresh = m.in, true
		mu.Unlock()
	}
	end := func() {
		if mux != nil {
			mux.stop() // first: closing its input frees a write that's waiting on it
			mu.Lock()
			current = nil
			mu.Unlock()
			mux = nil
		}
	}
	defer end()
	// The wall clock can jump (the box's starts in 2021, until ntpd sets it): the sound's timestamps
	// would jump with it, so ffmpeg starts again
	wall, mono := time.Now().Round(0), time.Now()
	tick := time.NewTicker(5 * time.Second)
	defer tick.Stop()
	for {
		var stopped <-chan struct{}
		if mux != nil {
			stopped = mux.done
		}
		select {
		case <-ctx.Done():
		case st, ok := <-statuses:
			if !ok {
				stop()
				break
			}
			end()
			again = nil
			if !st.Signal {
				now = nil
				c.signal.Store(nil)
				log.Print("HDMI: no signal")
				continue
			}
			now = &st
			sig := &Signal{Present: true, Width: st.Width, Height: st.Height, FPS: st.FPS, Rate: st.Rate}
			c.signal.Store(sig)
			if r := sig.StreamRate(); r != st.FPS {
				log.Printf("HDMI: %dx%d at %.2f fps, streamed at %.2f", st.Width, st.Height, st.FPS, r)
			} else {
				log.Printf("HDMI: %dx%d at %.2f fps", st.Width, st.Height, st.FPS)
			}
			begin()
			continue
		case <-stopped:
			log.Print("HDMI capture: ffmpeg stopped; starting it again")
			_ = mux.in.Close()
			mu.Lock()
			current = nil
			mu.Unlock()
			mux = nil
			again = time.After(time.Second)
			continue
		case <-again:
			again = nil
			if now != nil && mux == nil {
				begin()
			}
			continue
		case t := <-tick.C:
			moved := t.Round(0).Sub(wall) - t.Sub(mono)
			wall, mono = t.Round(0), t
			if (moved > time.Second || moved < -time.Second) && now != nil {
				log.Printf("HDMI capture: the clock moved %s: ffmpeg starts again", moved.Round(time.Second))
				end()
				begin()
			}
			continue
		}
		break
	}
	end()
	if err := helper.Wait(); err != nil && ctx.Err() == nil {
		return fmt.Errorf("the capture helper stopped: %w", err)
	}
	return nil
}

// muxer is one ffmpeg making MPEG-TS from the helper's H.264 and the HDMI sound.
type muxer struct {
	cmd  *exec.Cmd
	in   io.WriteCloser
	done chan struct{}
}

// SoundBuffer is where the box's sound capture buffer is set (its first card's, hw:0), in KiB. Luckfox's
// kernel makes it 16 (85 ms): a busy moment on the one core then loses sound, and it runs short of the
// picture (2% of it, here). It can only change while nothing's capturing.
var SoundBuffer = "/proc/asound/card0/pcm0c/sub0/prealloc"

// GrowSoundBuffer makes the sound capture buffer at least kib KiB, if the box has one to set.
func GrowSoundBuffer(kib int) {
	data, err := os.ReadFile(SoundBuffer)
	if err != nil {
		return // (not the box)
	}
	if now, err := strconv.Atoi(strings.TrimSpace(string(data))); err == nil && now < kib {
		if err := os.WriteFile(SoundBuffer, []byte(strconv.Itoa(kib)), 0); err != nil {
			log.Printf("HDMI sound: its buffer stays %d KiB: %v", now, err)
		}
	}
}

// WaitClockSet waits for the wall clock to be set (to be past 2021, where the box starts), up to most.
// It's a var so tests needn't wait.
var WaitClockSet = func(ctx context.Context, most time.Duration) {
	for end := time.Now().Add(most); time.Now().Year() < 2025 && time.Now().Before(end) && ctx.Err() == nil; {
		time.Sleep(500 * time.Millisecond)
	}
}

// MuxArgs is the ffmpeg command for a signal at fps: the picture as it is (copied), and the sound
// as AAC, in MPEG-TS with no added delay.
//
// With the box's sound (ALSA), the two start together: the sound's timestamps are the wall
// clock's (when its first sample came), and so are the picture's (when its first frame came), both
// kept (-copyts) and moved by start, any time about now. From there each counts on, frames and
// samples, so neither wanders. (Left to itself, ffmpeg started each at 0 when it got to it, and
// the sound was anywhere up to a second out, a different amount each time; and frames timed as
// they came bunch up after a keyframe.) ffmpeg barely looks at the picture before it opens the
// sound: its first keyframe has all it needs.
func MuxArgs(fps float64, audio []string, start time.Time) []string {
	args := []string{"-nostdin", "-hide_banner", "-loglevel", "error", "-fflags", "+genpts"}
	together := alsa(audio) && !start.IsZero()
	offset := []string{"-itsoffset", fmt.Sprintf("%.6f", -float64(start.UnixMicro())/1e6)}
	if together {
		args = append(args, "-copyts", "-thread_queue_size", "1024", "-use_wallclock_as_timestamps", "1")
		args = append(args, offset...)
	}
	args = append(args, "-probesize", "65536", "-analyzeduration", "0", "-f", "h264", "-framerate", Rate(fps), "-i", "pipe:0")
	if together {
		args = append(args, "-thread_queue_size", "1024")
		args = append(args, offset...)
	}
	args = append(args, audio...)
	args = append(args, "-map", "0:v", "-c:v", "copy")
	if together { // each frame a frame on from the first, in MPEG-TS's 90 kHz ticks
		args = append(args, "-bsf:v", "setts=ts=STARTPTS+N*"+strconv.FormatFloat(90000/rateOf(fps), 'f', -1, 64))
	}
	if len(audio) > 0 {
		args = append(args, "-map", "1:a")
		if together { // each sample a sample on from the first
			args = append(args, "-af", "asetpts=N/SR/TB+STARTPTS")
		}
		args = append(args, "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000")
	}
	// Each sound frame sent as it's made, in its own PES (ffmpeg gathers several otherwise): a server
	// that times each track by when it comes (MediaMTX's RTSP) then has the two arrive together
	return append(args, "-f", "mpegts", "-muxdelay", "0", "-muxpreload", "0", "-pes_payload_size", "0", "-flush_packets", "1", "pipe:1")
}

func (c *Capture) mux(ctx context.Context, fps float64, clock *carry, emit func([]byte)) (*muxer, error) {
	GrowSoundBuffer(256) // (1.4 s: before ffmpeg opens the card)
	if !clock.waited {   // the first time, the clock may not be set yet: its time's no good to go by
		clock.waited = true
		WaitClockSet(ctx, 20*time.Second)
	}
	cmd := exec.CommandContext(ctx, "ffmpeg", MuxArgs(fps, c.Audio, time.Now())...)
	in, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	out, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	m := &muxer{cmd: cmd, in: in, done: make(chan struct{})}
	clock.begin()
	go func() {
		lines := bufio.NewScanner(stderr)
		for lines.Scan() {
			log.Printf("HDMI capture: ffmpeg: %s", lines.Text())
		}
	}()
	go func() {
		defer close(m.done)
		r := bufio.NewReaderSize(out, 64<<10)
		var chunk []byte
		for {
			p := make([]byte, ts.PacketSize)
			if _, err := io.ReadFull(r, p); err != nil {
				break
			}
			if p[0] != ts.SyncByte {
				log.Print("HDMI capture: ffmpeg's MPEG-TS lost its place")
				break
			}
			for _, q := range clock.packet(p) {
				if chunk = append(chunk, q...); len(chunk) >= chunkPackets*ts.PacketSize {
					emit(chunk)
					chunk = nil
				}
			}
		}
		if len(chunk) > 0 {
			emit(chunk)
		}
		_ = cmd.Wait()
	}()
	return m, nil
}

// stop ends the ffmpeg: its input closed, it finishes what it has; if it doesn't, it's killed.
func (m *muxer) stop() {
	_ = m.in.Close()
	select {
	case <-m.done:
	case <-time.After(2 * time.Second):
		_ = m.cmd.Process.Kill()
		<-m.done
	}
}

// spsAt is where the first SPS (H.264's parameters, before each keyframe) starts in b, with its
// start code, or -1.
func spsAt(b []byte) int {
	for i := 0; i+3 < len(b); i++ {
		if b[i] == 0 && b[i+1] == 0 && b[i+2] == 1 && b[i+3]&0x1F == 7 {
			if i > 0 && b[i-1] == 0 {
				return i - 1 // a four-byte start code
			}
			return i
		}
	}
	return -1
}

// rateOf is fps as ffmpeg has it (Rate): the NTSC ones exactly.
func rateOf(fps float64) float64 {
	r := Rate(fps)
	if n, d, ok := strings.Cut(r, "/"); ok {
		a, _ := strconv.ParseFloat(n, 64)
		b, _ := strconv.ParseFloat(d, 64)
		return a / b
	}
	v, _ := strconv.ParseFloat(r, 64)
	return v
}

// alsa says whether the sound is the box's own (ALSA, with the wall clock's timestamps): the
// emulator's is made up (lavfi), starting at 0.
func alsa(audio []string) bool {
	for i, a := range audio {
		if a == "-f" && i+1 < len(audio) && audio[i+1] == "alsa" {
			return true
		}
	}
	return false
}

// Rate is a frame rate as ffmpeg takes it: the NTSC ones (59.94, 29.97, 23.976) exactly.
func Rate(fps float64) string {
	for _, n := range []float64{24, 30, 60} {
		if math.Abs(fps-n*1000/1001) < 0.02 {
			return fmt.Sprintf("%.0f000/1001", n)
		}
	}
	if fps <= 0 {
		return "30"
	}
	return strconv.FormatFloat(math.Round(fps*100)/100, 'f', -1, 64)
}

const wrapPCR = (1 << 33) * 300 // where a PCR goes back to 0 (26.5 hours)

// carry keeps the stream's clocks running forward from one ffmpeg to the next: each starts its
// own near 0, so each is moved to begin a second after the last one's.
type carry struct {
	waited  bool  // for the wall clock to be set, before the first ffmpeg
	offset  int64 // 27 MHz ticks added to this ffmpeg's clocks
	last    int64 // the last PCR sent, moved
	started bool
	waiting [][]byte // this ffmpeg's packets before its first PCR
	align   aligner
}

func (c *carry) begin() {
	c.started, c.waiting, c.align = false, nil, aligner{soundAt: -1, firstKey: -1, from: -1}
}

// packet moves p's clocks and returns what can go out: nothing until this ffmpeg's first PCR
// (which sets how far to move them), then everything; all from where its picture and sound both
// start (aligner).
func (c *carry) packet(p []byte) [][]byte {
	if !c.align.keep(p) {
		return nil
	}
	if !c.started {
		pcr, ok := ts.PCR(p)
		if !ok {
			if len(c.waiting) < 4096 {
				c.waiting = append(c.waiting, p)
			}
			return nil
		}
		c.offset = ((c.last+ts.PCRHz-pcr)%wrapPCR + wrapPCR) % wrapPCR
		c.started = true
		out := append(c.waiting, p)
		c.waiting = nil
		for _, q := range out {
			c.move(q)
		}
		return out
	}
	c.move(p)
	return [][]byte{p}
}

// aligner starts each ffmpeg's stream with its picture and its sound together. The sound starts a
// little after the picture (ffmpeg opens the card once it has seen the first keyframe), and a
// server that starts each track at its first packet (MediaMTX does, for RTSP and RTMP, which the
// hub restreams from) would keep them that far out of step, a different amount each time: half a
// second, here. So the stream starts at the first keyframe once the sound has begun, and the sound
// from there; with no sound within 3 s of picture, at a keyframe all the same.
type aligner struct {
	tables    ts.Tables
	soundAt   int64 // the first sound's PTS (-1: none yet)
	firstKey  int64 // the first keyframe's PTS (-1: none yet)
	from      int64 // the keyframe the stream starts at (-1: not yet)
	dropSound bool  // the sound packet now is from before it
}

func (a *aligner) keep(p []byte) bool {
	a.tables.Note(p)
	switch pid := ts.PID(p); {
	case a.tables.Table(p) || a.tables.VideoPID == 0:
		return true // (the tables; or before them, as ffmpeg sends them first)
	case a.tables.AudioPID == 0:
		return a.from >= 0 || a.startAt(p, false) // no sound: from the first keyframe
	case a.from < 0 && pid == a.tables.AudioPID:
		if pts, ok := ts.PTS(p); ok && a.soundAt < 0 {
			a.soundAt = pts
		}
		return false
	case a.from < 0:
		return a.startAt(p, true)
	case pid == a.tables.AudioPID:
		if pts, ok := ts.PTS(p); ok {
			a.dropSound = !atOrAfter(pts, a.from)
		}
		return !a.dropSound
	}
	return true
}

// startAt says whether the stream starts at p: a keyframe, once the sound has (if it's waited for).
func (a *aligner) startAt(p []byte, sound bool) bool {
	if ts.PID(p) != a.tables.VideoPID || !a.tables.Ready(p) {
		return false
	}
	pts, ok := ts.PTS(p)
	if !ok {
		return false
	}
	if a.firstKey < 0 {
		a.firstKey = pts
	}
	if !sound || a.soundAt >= 0 && atOrAfter(pts, a.soundAt) || !atOrAfter(a.firstKey+3*ts.PTSHz, pts) {
		a.from = pts
		return true
	}
	return false
}

// atOrAfter compares two 90 kHz timestamps across the 33-bit wrap.
func atOrAfter(a, b int64) bool { return (a-b)&(1<<33-1) < 1<<32 }

func (c *carry) move(p []byte) {
	ts.Shift(p, c.offset)
	if pcr, ok := ts.PCR(p); ok {
		c.last = pcr
	}
}
