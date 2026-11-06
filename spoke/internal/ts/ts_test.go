package ts

import "testing"

func pesPacket(pts, dts int64) []byte {
	p := make([]byte, PacketSize)
	p[0], p[1], p[2], p[3] = SyncByte, 0x41, 0x00, 0x30 // PUSI, PID 0x100, adaptation + payload
	p[4] = 7
	p[5] = 0x50 // random access, PCR
	pes := p[12:]
	copy(pes, []byte{0, 0, 1, 0xE0, 0, 0, 0x80, 0xC0, 10})
	pes[9], pes[14] = 0x31, 0x11
	writeStamp(pes[9:14], pts)
	writeStamp(pes[14:19], dts)
	setPCR(p, dts*300)
	return p
}

func TestShiftMovesEveryClock(t *testing.T) {
	p := pesPacket(900_000, 897_000)
	Shift(p, 30*PCRHz)
	pes := Payload(p)
	if got := readStamp(pes[9:14]); got != 900_000+30*PTSHz {
		t.Fatalf("PTS %d", got)
	}
	if got := readStamp(pes[14:19]); got != 897_000+30*PTSHz {
		t.Fatalf("DTS %d", got)
	}
	if got, _ := PCR(p); got != (897_000+30*PTSHz)*300 {
		t.Fatalf("PCR %d", got)
	}
	if !RandomAccess(p) || pes[9]>>4 != 3 || pes[14]>>4 != 1 {
		t.Fatal("flags or stamp prefixes changed")
	}
}

func TestStampsWrapLikeAnEncoder(t *testing.T) {
	p := pesPacket(wrap90k-10, wrap90k-10)
	Shift(p, 20*300)
	if got := readStamp(Payload(p)[9:14]); got != 10 {
		t.Fatalf("PTS %d, want it to wrap to 10", got)
	}
}
