package media

import (
	"slices"
	"testing"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/ts"
)

// pcrPacket is a packet with a PCR (27 MHz ticks) and no PES.
func pcrPacket(pcr int64) []byte {
	p := make([]byte, ts.PacketSize)
	p[0], p[1], p[2], p[3] = ts.SyncByte, 0x01, 0x00, 0x30
	p[4], p[5] = 7, 0x10 // adaptation field: a PCR
	base, ext := pcr/300, pcr%300
	p[6], p[7], p[8], p[9] = byte(base>>25), byte(base>>17), byte(base>>9), byte(base>>1)
	p[10], p[11] = byte(base&1)<<7|0x7E|byte(ext>>8)&1, byte(ext)
	return p
}

func TestEachFfmpegCarriesOnFromTheLast(t *testing.T) {
	var c carry
	table := make([]byte, ts.PacketSize)
	table[0] = ts.SyncByte
	pcrs := func(ps [][]byte) (out []int64) {
		for _, p := range ps {
			if v, ok := ts.PCR(p); ok {
				out = append(out, v)
			}
		}
		return
	}
	c.begin()
	if out := c.packet(table); out != nil {
		t.Fatal("sent something before the first clock")
	}
	first := c.packet(pcrPacket(5 * ts.PCRHz)) // the tables, then the clock, together
	if len(first) != 2 || pcrs(first)[0] != ts.PCRHz {
		t.Fatalf("the first ffmpeg starts at %v", pcrs(first))
	}
	c.packet(pcrPacket(15 * ts.PCRHz)) // ten seconds in: 11 s, moved
	c.begin()                          // the cable out and in: a new ffmpeg, its clock near 0 again
	again := c.packet(pcrPacket(ts.PCRHz / 10))
	if got := pcrs(again)[0]; got != 12*ts.PCRHz {
		t.Fatalf("the next ffmpeg starts at %.1f s, not a second after the last (11 s)", float64(got)/ts.PCRHz)
	}
	// Near the clock's wrap (26.5 hours in), it still moves forward
	c.last = wrapPCR - ts.PCRHz/2
	c.begin()
	if got := pcrs(c.packet(pcrPacket(0)))[0]; got != ts.PCRHz/2 {
		t.Fatalf("across the wrap: %d", got)
	}
}

func TestFrameRatesForFfmpeg(t *testing.T) {
	for fps, want := range map[float64]string{59.94: "60000/1001", 29.97: "30000/1001", 23.976: "24000/1001",
		60: "60", 50: "50", 25: "25", 0: "30"} {
		if got := Rate(fps); got != want {
			t.Errorf("Rate(%v) = %s, want %s", fps, got, want)
		}
	}
	args := MuxArgs(60, []string{"-f", "alsa", "-i", "hw:0"})
	if !slices.Contains(args, "aac") || args[len(args)-1] != "pipe:1" || !slices.Contains(args, "hw:0") {
		t.Fatalf("%v", args)
	}
	if args := MuxArgs(30, nil); slices.Contains(args, "aac") {
		t.Fatalf("no sound, but %v", args)
	}
}

func TestANewFfmpegStartsAtTheParameters(t *testing.T) {
	slice := []byte{0, 0, 1, 0x41, 9, 9}        // a P-frame's slice
	sps := []byte{0, 0, 0, 1, 0x67, 0x4D, 0x40} // an SPS, four-byte start code
	if got := spsAt(append(append([]byte{}, slice...), sps...)); got != len(slice) {
		t.Fatalf("at %d", got)
	}
	if spsAt(slice) != -1 || spsAt([]byte{0, 0, 1, 0x67}) != 0 {
		t.Fatal("found one that isn't there, or missed one at the start")
	}
}
