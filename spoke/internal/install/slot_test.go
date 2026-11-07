package install

import (
	"encoding/binary"
	"encoding/hex"
	"hash/crc32"
	"os"
	"path/filepath"
	"testing"
)

// The box's own, as it was: slot A out of tries, B started (7 tries), neither marked successful.
const boxSlots = "00414230010000000f0000000f0700000100000000000000000000000602b30a"

func misc(t *testing.T) string {
	data := make([]byte, 4096)
	ab, _ := hex.DecodeString(boxSlots)
	copy(data[slotAt:], ab)
	path := filepath.Join(t.TempDir(), "misc")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func slots(t *testing.T, path string) []byte {
	data, _ := os.ReadFile(path)
	ab := data[slotAt : slotAt+32]
	if crc32.ChecksumIEEE(ab[:28]) != binary.BigEndian.Uint32(ab[28:]) {
		t.Fatal("bad CRC")
	}
	return ab
}

func TestTheSlotItStartedFromIsMarkedGood(t *testing.T) {
	path := misc(t)
	did, err := MarkGood(path, "user_debug=31 android_slotsufix=_b rootwait root=/dev/mmcblk0p10")
	if err != nil || !did {
		t.Fatalf("%v %v", did, err)
	}
	ab := slots(t, path)
	if b := ab[12:16]; b[0] != 15 || b[1] != 0 || b[2] != 1 {
		t.Errorf("slot B: % x", b)
	}
	if a := ab[8:12]; a[1] != 0 || a[2] != 0 {
		t.Errorf("slot A changed: % x", a)
	}
	if did, _ := MarkGood(path, "android_slotsufix=_b"); did {
		t.Error("marked again")
	}
}

func TestNotStartedAOrBOrDamaged(t *testing.T) {
	path := misc(t)
	if did, err := MarkGood(path, "root=/dev/mmcblk1p9"); did || err != nil {
		t.Fatalf("no slot: %v %v", did, err)
	}
	data, _ := os.ReadFile(path)
	data[slotAt+9] = 3 // A's tries, without its CRC
	os.WriteFile(path, data, 0o600)
	if _, err := MarkGood(path, "android_slotsufix=_a"); err == nil {
		t.Error("damaged: no error")
	}
}
