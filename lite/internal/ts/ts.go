// Package ts reads and adjusts MPEG-TS packets: just enough to pace a stream, shift its
// timestamps, and find where a part can start (a video keyframe, with the stream's tables).
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package ts

const (
	PacketSize = 188
	SyncByte   = 0x47
	PCRHz      = 27_000_000 // PCR ticks per second
	PTSHz      = 90_000     // PTS/DTS ticks per second
	wrap90k    = 1 << 33    // PTS, DTS and the PCR base are 33-bit counters
)

func PID(p []byte) uint16 { return uint16(p[1]&0x1F)<<8 | uint16(p[2]) }

// Start reports whether a PES packet or table section starts in this packet.
func Start(p []byte) bool { return p[1]&0x40 != 0 }

func adaptation(p []byte) []byte {
	if p[3]&0x20 == 0 || p[4] == 0 || int(p[4]) > PacketSize-5 {
		return nil
	}
	return p[5 : 5+int(p[4])]
}

// Payload is the packet's data after the header and adaptation field, if it has any.
func Payload(p []byte) []byte {
	if p[3]&0x10 == 0 {
		return nil
	}
	at := 4
	if p[3]&0x20 != 0 {
		at += 1 + int(p[4])
	}
	if at >= PacketSize {
		return nil
	}
	return p[at:]
}

// RandomAccess reports the encoder's mark for a frame a decoder can start from (a keyframe).
func RandomAccess(p []byte) bool {
	a := adaptation(p)
	return a != nil && a[0]&0x40 != 0
}

// PCR returns the packet's program clock reference in 27 MHz ticks.
func PCR(p []byte) (int64, bool) {
	a := adaptation(p)
	if len(a) < 7 || a[0]&0x10 == 0 {
		return 0, false
	}
	base := int64(a[1])<<25 | int64(a[2])<<17 | int64(a[3])<<9 | int64(a[4])<<1 | int64(a[5]>>7)
	ext := int64(a[5]&1)<<8 | int64(a[6])
	return base*300 + ext, true
}

func setPCR(p []byte, v int64) {
	a := adaptation(p)
	base, ext := (v/300)%wrap90k, v%300
	a[1], a[2], a[3], a[4] = byte(base>>25), byte(base>>17), byte(base>>9), byte(base>>1)
	a[5] = byte(base&1)<<7 | 0x7E | byte(ext>>8)&1
	a[6] = byte(ext)
}

func readStamp(b []byte) int64 {
	return int64(b[0]>>1&0x07)<<30 | int64(b[1])<<22 | int64(b[2]>>1)<<15 | int64(b[3])<<7 | int64(b[4]>>1)
}

func writeStamp(b []byte, v int64) {
	v %= wrap90k
	b[0] = b[0]&0xF0 | byte(v>>29)&0x0E | 1
	b[1] = byte(v >> 22)
	b[2] = byte(v>>14)&0xFE | 1
	b[3] = byte(v >> 7)
	b[4] = byte(v<<1)&0xFE | 1
}

// Shift moves the packet's PCR and any PTS/DTS later by `by` 27 MHz ticks, in place.
// Looping a clip with it keeps time running forward, as a live encoder's would.
func Shift(p []byte, by int64) {
	if by == 0 {
		return
	}
	if v, ok := PCR(p); ok {
		setPCR(p, (v+by)%(wrap90k*300))
	}
	if !Start(p) {
		return
	}
	pes := Payload(p)
	if len(pes) < 14 || pes[0] != 0 || pes[1] != 0 || pes[2] != 1 {
		return
	}
	flags := pes[7] >> 6 // 2: PTS, 3: PTS and DTS
	if flags&2 != 0 {
		writeStamp(pes[9:14], readStamp(pes[9:14])+by/300)
	}
	if flags == 3 && len(pes) >= 19 {
		writeStamp(pes[14:19], readStamp(pes[14:19])+by/300)
	}
}

// DTS returns the decode time (90 kHz) of the frame a PES packet starts here: its DTS, or its
// PTS when the two are the same.
func DTS(p []byte) (int64, bool) {
	if !Start(p) {
		return 0, false
	}
	pes := Payload(p)
	if len(pes) < 14 || pes[0] != 0 || pes[1] != 0 || pes[2] != 1 || pes[7]>>6&2 == 0 {
		return 0, false
	}
	if pes[7]>>6 == 3 && len(pes) >= 19 {
		return readStamp(pes[14:19]), true
	}
	return readStamp(pes[9:14]), true
}

// Tables remembers the stream's PAT and PMT, so a part can start with them, and which
// PID carries the video.
type Tables struct {
	PAT, PMT []byte
	pmtPID   uint16
	VideoPID uint16
}

// Note looks at one packet and keeps any table in it.
func (t *Tables) Note(p []byte) {
	pid := PID(p)
	switch {
	case pid == 0 && Start(p):
		t.PAT = append(t.PAT[:0], p...)
		if s := section(p); len(s) >= 12 {
			t.pmtPID = uint16(s[10]&0x1F)<<8 | uint16(s[11]) // the first program's PMT
		}
	case pid == t.pmtPID && pid != 0 && Start(p):
		t.PMT = append(t.PMT[:0], p...)
		t.VideoPID = videoPID(section(p))
	}
}

// Ready reports whether a part could start here: the tables are known and a keyframe begins.
func (t *Tables) Ready(p []byte) bool {
	return t.PAT != nil && t.PMT != nil && PID(p) == t.VideoPID && Start(p) && RandomAccess(p)
}

func section(p []byte) []byte {
	pl := Payload(p)
	if len(pl) < 1 || int(pl[0])+1 >= len(pl) {
		return nil
	}
	return pl[1+int(pl[0]):] // after the pointer field
}

func videoPID(s []byte) uint16 {
	if len(s) < 12 {
		return 0
	}
	end := 3 + (int(s[1]&0x0F)<<8 | int(s[2])) - 4 // section length, less the CRC
	at := 12 + (int(s[10]&0x0F)<<8 | int(s[11]))   // after the program info
	for at+5 <= end && at+5 <= len(s) {
		kind, pid := s[at], uint16(s[at+1]&0x1F)<<8|uint16(s[at+2])
		if kind == 0x1B || kind == 0x24 { // H.264, H.265
			return pid
		}
		at += 5 + (int(s[at+3]&0x0F)<<8 | int(s[at+4]))
	}
	return 0
}
