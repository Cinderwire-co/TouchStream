package media

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math"
	"os/exec"
	"strconv"
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

	// The picture goes to whichever ffmpeg is making the stream (none while there's no signal),
	// from a keyframe's parameters (SPS) on: before them it couldn't make sense of it
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

// MuxArgs is the ffmpeg command for a signal at fps: the picture as it is (copied), and the sound
// as AAC, in MPEG-TS with no added delay.
func MuxArgs(fps float64, audio []string) []string {
	args := []string{"-nostdin", "-hide_banner", "-loglevel", "error", "-fflags", "+genpts",
		"-f", "h264", "-framerate", Rate(fps), "-i", "pipe:0"}
	args = append(args, audio...)
	args = append(args, "-map", "0:v", "-c:v", "copy")
	if len(audio) > 0 {
		args = append(args, "-map", "1:a", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000")
	}
	return append(args, "-f", "mpegts", "-muxdelay", "0", "-muxpreload", "0", "pipe:1")
}

func (c *Capture) mux(ctx context.Context, fps float64, clock *carry, emit func([]byte)) (*muxer, error) {
	cmd := exec.CommandContext(ctx, "ffmpeg", MuxArgs(fps, c.Audio)...)
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
	offset  int64 // 27 MHz ticks added to this ffmpeg's clocks
	last    int64 // the last PCR sent, moved
	started bool
	waiting [][]byte // this ffmpeg's packets before its first PCR
}

func (c *carry) begin() { c.started, c.waiting = false, nil }

// packet moves p's clocks and returns what can go out: nothing until this ffmpeg's first PCR
// (which sets how far to move them), then everything.
func (c *carry) packet(p []byte) [][]byte {
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

func (c *carry) move(p []byte) {
	ts.Shift(p, c.offset)
	if pcr, ok := ts.PCR(p); ok {
		c.last = pcr
	}
}
