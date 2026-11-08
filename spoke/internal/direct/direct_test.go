package direct

import (
	"bytes"
	"strings"
	"testing"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/ts"
)

func TestDueFollowsTheSchedule(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	end := float64(now.Unix() + 3600)
	off := false
	p := Plan{Schedule: []Event{
		{ID: 1, StartAt: float64(now.Unix() - 7200), EndAt: ptr(float64(now.Unix() - 3600))}, // over
		{ID: 2, StartAt: float64(now.Unix() + 5), EndAt: &end},                               // starts in 5 s: within the lead
		{ID: 3, StartAt: float64(now.Unix() - 60), AutoStart: &off},                          // started by hand only
	}}
	if e := p.Due(now); e == nil || e.ID != 2 {
		t.Fatalf("due: %+v", e)
	}
	if e := p.Due(now.Add(2 * time.Hour)); e != nil {
		t.Fatalf("past its end: %+v", e)
	}
	open := Plan{Schedule: []Event{{ID: 4, StartAt: float64(now.Unix())}}}
	if open.Due(now.Add(3*time.Hour)) == nil || open.Due(now.Add(5*time.Hour)) != nil {
		t.Fatal("a broadcast with no end should run for 4 hours")
	}
}

func TestTheStreamGoesToEveryTargetUntouched(t *testing.T) {
	args := strings.Join(Args([]Target{{URL: "rtmp://a.rtmp.youtube.com/live2/", Key: "abcd"}, {URL: "rtmps://b/app", Key: "k"}}), " ")
	for _, want := range []string{"-c copy", "-tag:v 7 -tag:a 10", "[f=flv:onfail=ignore:bsfs/a=aac_adtstoasc]rtmp://a.rtmp.youtube.com/live2/abcd|[f=flv:onfail=ignore:bsfs/a=aac_adtstoasc]rtmps://b/app/k"} {
		if !strings.Contains(args, want) {
			t.Fatalf("missing %q in %s", want, args)
		}
	}
}

func ptr(v float64) *float64 { return &v }

// What ffmpeg 4.4 (on the box) and later ones say, streaming through the tee muxer
func TestSendingFromProgress(t *testing.T) {
	for line, want := range map[string]bool{
		"total_size=N/A": false, "out_time_us=0": false, "frame=1": false, "out_time_us=N/A": false,
		"out_time_us=566667": true, // 4.4: total_size stays N/A, but the clock moves
		"total_size=48":      true, // later ffmpegs
		"total_size=0":       false,
	} {
		if got := Sending(line); got != want {
			t.Errorf("Sending(%q) = %v, want %v", line, got, want)
		}
	}
}

func packet(pid uint16, start, keyframe bool, payload []byte) []byte {
	p := make([]byte, ts.PacketSize)
	p[0], p[1], p[2], p[3] = ts.SyncByte, byte(pid>>8), byte(pid), 0x10
	if start {
		p[1] |= 0x40
	}
	at := 4
	if keyframe {
		p[3] |= 0x20
		p[4], p[5] = 1, 0x40 // adaptation field: random access
		at = 6
	}
	copy(p[at:], payload)
	return p
}

// An ffmpeg started part way through gets nothing until a keyframe, then the tables first
func TestAStreamStartsAtAKeyframeWithItsTables(t *testing.T) {
	pat := packet(0, true, false, []byte{0, 0x00, 0xB0, 13, 0, 1, 0xC1, 0, 0, 0, 1, 0xF0, 0x00})
	pmt := packet(0x1000, true, false, []byte{0, 0x02, 0xB0, 18, 0, 1, 0xC1, 0, 0, 0xE1, 0, 0xF0, 0,
		0x1B, 0xE1, 0x00, 0xF0, 0x00, 0, 0, 0, 0})
	frame := []byte{0, 0, 1, 0xE0, 0, 0, 0x80, 0x00, 0}
	middle, key := packet(0x100, true, false, frame), packet(0x100, true, true, frame)
	join := func(ps ...[]byte) (b []byte) {
		for _, p := range ps {
			b = append(b, p...)
		}
		return
	}
	var sp startPoint
	if out := sp.next(join(pat, pmt, middle)); out != nil {
		t.Fatalf("sent %d packets before a keyframe", len(out)/ts.PacketSize)
	}
	if out := sp.next(join(middle, key, middle)); !bytes.Equal(out, join(pat, pmt, key, middle)) {
		t.Fatalf("started with %d packets, not the tables and the keyframe", len(out)/ts.PacketSize)
	}
	if out := sp.next(middle); !bytes.Equal(out, middle) {
		t.Fatal("after the start, everything goes through")
	}
}
