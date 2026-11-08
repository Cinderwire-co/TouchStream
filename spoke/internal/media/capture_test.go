package media

import (
	"slices"
	"strings"
	"testing"
	"time"

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
	args := MuxArgs(60, []string{"-f", "alsa", "-i", "hw:0"}, time.Time{})
	if !slices.Contains(args, "aac") || args[len(args)-1] != "pipe:1" || !slices.Contains(args, "hw:0") {
		t.Fatalf("%v", args)
	}
	if args := MuxArgs(30, nil, time.Time{}); slices.Contains(args, "aac") {
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

func TestTheBoxsSoundAndPictureStartTogether(t *testing.T) {
	start := time.UnixMicro(1794100000123456)
	args := strings.Join(MuxArgs(30, []string{"-f", "alsa", "-ar", "48000", "-i", "hw:0"}, start), " ")
	// Both on the wall clock, moved by start, then counted: frames, and samples
	for _, want := range []string{" -copyts ", "-use_wallclock_as_timestamps 1 -itsoffset -1794100000.123456 ",
		"-itsoffset -1794100000.123456 -f alsa", "setts=ts=STARTPTS+N*3000", "asetpts=N/SR/TB+STARTPTS"} {
		if !strings.Contains(args, want) {
			t.Fatalf("box: no %q in %s", want, args)
		}
	}
	if args := strings.Join(MuxArgs(59.94/2, []string{"-f", "alsa", "-i", "hw:0"}, start), " "); !strings.Contains(args, "N*3003") {
		t.Fatalf("29.97: %s", args)
	}
	// The emulator's sound is made up, from 0: left as it was
	args = strings.Join(MuxArgs(30, []string{"-re", "-f", "lavfi", "-i", "sine=frequency=440"}, start), " ")
	if strings.Contains(args, "-copyts") || strings.Contains(args, "setts") || strings.Contains(args, "asetpts") {
		t.Fatalf("emulator: %s", args)
	}
}

// A stream as ffmpeg makes it: a PAT, a PMT (H.264 on 0x100, AAC on 0x101), then PES packets.
const videoPID, audioPID, pmtPID = 0x100, 0x101, 0x1000

func packet(pid uint16, start bool) []byte {
	p := make([]byte, ts.PacketSize)
	p[0], p[1], p[2], p[3] = ts.SyncByte, byte(pid>>8), byte(pid), 0x10
	if start {
		p[1] |= 0x40
	}
	return p
}

func tablePacket(pid uint16, section []byte) []byte {
	p := packet(pid, true)
	copy(p[5:], section) // after the pointer field (0)
	return p
}

func pat() []byte {
	return tablePacket(0, []byte{0x00, 0xB0, 13, 0, 1, 0xC1, 0, 0, 0, 1, 0xE0 | pmtPID>>8, pmtPID & 0xFF, 0, 0, 0, 0})
}

func pmt() []byte {
	return tablePacket(pmtPID, []byte{0x02, 0xB0, 23, 0, 1, 0xC1, 0, 0, 0xE0 | videoPID>>8, videoPID & 0xFF, 0xF0, 0,
		0x1B, 0xE0 | videoPID>>8, videoPID & 0xFF, 0xF0, 0, 0x0F, 0xE0 | audioPID>>8, audioPID & 0xFF, 0xF0, 0, 0, 0, 0, 0})
}

// pes starts a frame at pts (90 kHz); a keyframe has the encoder's mark and a PCR.
func pes(pid uint16, pts int64, key bool) []byte {
	p := packet(pid, true)
	at := 4
	if key {
		p[3] = 0x30
		pcr := pts * 300
		base := pcr / 300
		p[4], p[5] = 7, 0x50 // adaptation field: the mark, and a PCR
		p[6], p[7], p[8], p[9] = byte(base>>25), byte(base>>17), byte(base>>9), byte(base>>1)
		p[10], p[11] = byte(base&1)<<7|0x7E, 0
		at = 12
	}
	id := byte(0xE0)
	if pid == audioPID {
		id = 0xC0
	}
	copy(p[at:], []byte{0, 0, 1, id, 0, 0, 0x80, 0x80, 5,
		byte(0x21 | (pts>>29)&0x0E), byte(pts >> 22), byte(0x01 | (pts>>14)&0xFE), byte(pts >> 7), byte(0x01 | (pts<<1)&0xFE)})
	return p
}

func TestEachStreamStartsWithItsPictureAndSoundTogether(t *testing.T) {
	var c carry
	c.begin()
	in := [][]byte{pat(), pmt(),
		pes(videoPID, 0, true), pes(videoPID, 3000, false), // the picture first
		pes(audioPID, 27000, false), pes(videoPID, 6000, false), pes(audioPID, 28920, false), // the sound 0.3 s on
		pes(videoPID, 180000, true), pes(audioPID, 178080, false), pes(audioPID, 180000, false), pes(videoPID, 183000, false)}
	var out [][]byte
	for _, p := range in {
		out = append(out, c.packet(p)...)
	}
	var got []string
	var first map[uint16]int64 = map[uint16]int64{}
	for _, p := range out {
		pid := ts.PID(p)
		got = append(got, map[uint16]string{0: "PAT", pmtPID: "PMT", videoPID: "video", audioPID: "sound"}[pid])
		if v, ok := ts.PTS(p); ok {
			if _, seen := first[pid]; !seen {
				first[pid] = v
			}
		}
	}
	// The tables, then the keyframe at 2 s (the first once the sound's going), the sound from 2 s, and on
	if strings.Join(got, " ") != "PAT PMT video sound video" {
		t.Fatalf("sent %v", got)
	}
	if first[videoPID] != first[audioPID] {
		t.Fatalf("the picture starts at %d, the sound at %d", first[videoPID], first[audioPID])
	}
}

func TestAStreamWithNoSoundStartsAtItsFirstKeyframe(t *testing.T) {
	var c carry
	c.begin()
	noSound := tablePacket(pmtPID, []byte{0x02, 0xB0, 18, 0, 1, 0xC1, 0, 0, 0xE0 | videoPID>>8, videoPID & 0xFF, 0xF0, 0,
		0x1B, 0xE0 | videoPID>>8, videoPID & 0xFF, 0xF0, 0, 0, 0, 0, 0})
	var out [][]byte
	for _, p := range [][]byte{pat(), noSound, pes(videoPID, 3000, false), pes(videoPID, 6000, true), pes(videoPID, 9000, false)} {
		out = append(out, c.packet(p)...)
	}
	if len(out) != 4 { // the tables, the keyframe, and the frame after it
		t.Fatalf("sent %d packets", len(out))
	}
}
