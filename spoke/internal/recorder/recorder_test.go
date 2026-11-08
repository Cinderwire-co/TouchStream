package recorder

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/ts"
)

// packet makes a TS packet: a table section, or the start (or middle) of a video frame.
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

var (
	pat = packet(0, true, false, []byte{0, 0x00, 0xB0, 13, 0, 1, 0xC1, 0, 0, 0, 1, 0xF0, 0x00})
	pmt = packet(0x1000, true, false, []byte{0, 0x02, 0xB0, 18, 0, 1, 0xC1, 0, 0, 0xE1, 0, 0xF0, 0,
		0x1B, 0xE1, 0x00, 0xF0, 0x00, 0, 0, 0, 0})
	frame = []byte{0, 0, 1, 0xE0, 0, 0, 0x80, 0x00, 0}
)

func TestARecordingStartsOnAKeyframeAndEndsOnAWholeFrame(t *testing.T) {
	r := &Recorder{Dir: t.TempDir()}
	session, err := r.Start()
	if err != nil {
		t.Fatal(err)
	}
	r.write(append(append([]byte{}, pat...), pmt...))
	r.write(packet(0x100, true, false, frame)) // not a keyframe: nothing to start from yet
	key := packet(0x100, true, true, frame)
	middle := packet(0x100, false, false, []byte{1, 2, 3})
	r.write(append(append([]byte{}, key...), middle...))

	stopped := make(chan struct{})
	go func() { r.Stop(); close(stopped) }()
	time.Sleep(50 * time.Millisecond)
	r.write(middle)                            // the frame in progress still gets written
	r.write(packet(0x100, true, false, frame)) // the next frame: the recording ends before it
	<-stopped

	data, err := os.ReadFile(filepath.Join(session, "part-00000.ts"))
	if err != nil {
		t.Fatal(err)
	}
	if got := len(data) / ts.PacketSize; got != 5 { // PAT, PMT, the keyframe and its two middles
		t.Fatalf("part has %d packets, want 5", got)
	}
	if string(data[2*ts.PacketSize:3*ts.PacketSize]) != string(key) {
		t.Fatal("the part doesn't start (after its tables) with the keyframe")
	}
	if _, err := os.Stat(filepath.Join(session, ".complete")); err != nil {
		t.Fatal("the session isn't marked complete")
	}
	if r.Recording() {
		t.Fatal("still recording")
	}
}
