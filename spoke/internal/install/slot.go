package install

import (
	"bytes"
	"encoding/binary"
	"errors"
	"hash/crc32"
	"os"
	"strings"
)

// The box starts from one of two copies of its system (A and B), as Android does: U-Boot takes a
// try from the slot it starts until the system says it started well, and once a slot's out of
// tries, it starts the other. Luckfox's apps said so; the spoke does instead, once it's up
// (MarkGood). The slots are in the misc partition, 2 KiB in (AvbABData, with Rockchip's last_boot).

const (
	Misc   = "/dev/block/by-name/misc"
	slotAt = 2048
)

var abMagic = []byte("\x00AB0")

// MarkGood marks the slot the box started from (cmdline: /proc/cmdline's) as started well, in
// misc. It says whether it changed anything.
func MarkGood(misc, cmdline string) (bool, error) {
	slot := -1
	for _, arg := range strings.Fields(cmdline) {
		switch arg {
		case "android_slotsufix=_a":
			slot = 0
		case "android_slotsufix=_b":
			slot = 1
		}
	}
	if slot < 0 {
		return false, nil // (not started A/B)
	}
	f, err := os.OpenFile(misc, os.O_RDWR, 0)
	if err != nil {
		return false, err
	}
	defer f.Close()
	ab := make([]byte, 32)
	if _, err := f.ReadAt(ab, slotAt); err != nil {
		return false, err
	}
	if !bytes.Equal(ab[:4], abMagic) || crc32.ChecksumIEEE(ab[:28]) != binary.BigEndian.Uint32(ab[28:]) {
		return false, errors.New("the A/B slots in misc aren't there, or are damaged")
	}
	s := ab[8+4*slot:]
	if s[2] == 1 { // successful already
		return false, nil
	}
	s[1], s[2] = 0, 1 // no tries needed now: it's successful
	binary.BigEndian.PutUint32(ab[28:], crc32.ChecksumIEEE(ab[:28]))
	if _, err := f.WriteAt(ab, slotAt); err != nil {
		return false, err
	}
	return true, f.Sync()
}
