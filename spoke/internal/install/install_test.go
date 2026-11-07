package install

import (
	"bytes"
	"encoding/binary"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A card as the installer leaves it, with the image's layout but small partitions, each filled
// with a byte of its own; and an eMMC of Luckfox's (all 0xEE).
const spec = "mmcblk1:32K(env),512K@32K(idblock),512K(uboot_a),512K(uboot_b),256K(misc),1M(security)," +
	"1M@2944K(boot_a),1M(boot_b),2M(system_a),2M(system_b),-(userdata)"

func disks(t *testing.T, emmcSize int64) (card, emmc string, parts map[string]part) {
	t.Helper()
	dir := t.TempDir()
	parts, err := layout(spec, 12<<20)
	if err != nil {
		t.Fatal(err)
	}
	data := make([]byte, 12<<20)
	fill := byte(1)
	for name, p := range parts {
		if name != "env" && name != "userdata" {
			copy(data[p.At:p.At+p.Size], bytes.Repeat([]byte{fill}, int(p.Size)))
			fill++
		}
	}
	block, err := env{"blkdevparts=" + spec, "sys_bootargs= root=/dev/mmcblk1p9 rootfstype=ext4 fbcon=map:1",
		"sd_parts=mmcblk0:16K@512(env)"}.bytes()
	if err != nil {
		t.Fatal(err)
	}
	copy(data, block)
	card, emmc = filepath.Join(dir, "card.img"), filepath.Join(dir, "emmc.img")
	if err := os.WriteFile(card, data, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(emmc, bytes.Repeat([]byte{0xEE}, int(emmcSize)), 0o600); err != nil {
		t.Fatal(err)
	}
	return card, emmc, parts
}

func stubs(t *testing.T) *[]string {
	var did []string
	oldData, oldHold := makeData, holdStill
	makeData = func(emmc string, at int64, settings string) error {
		did = append(did, "data "+settings)
		return nil
	}
	holdStill = func() (func(), error) {
		did = append(did, "still")
		return func() { did = append(did, "released") }, nil
	}
	t.Cleanup(func() { makeData, holdStill = oldData, oldHold })
	return &did
}

func region(t *testing.T, path string, p part) []byte {
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return data[p.At : p.At+p.Size]
}

func TestTheSystemGoesOntoTheEMMCAndTheCardStepsAside(t *testing.T) {
	card, emmc, parts := disks(t, 16<<20+minData)
	did := stubs(t)
	before := map[string][]byte{}
	for name, p := range parts {
		before[name] = append([]byte{}, region(t, card, p)...)
	}
	var said []string
	if err := ToEMMC(card, emmc, "/userdata/touchstream", Options{}, func(s string) { said = append(said, s) }); err != nil {
		t.Fatal(err)
	}
	for _, name := range system {
		if !bytes.Equal(region(t, emmc, parts[name]), before[name]) {
			t.Errorf("%s isn't the card's", name)
		}
	}
	for _, name := range Bootloader { // the eMMC's own boot loader stays
		if !bytes.Equal(region(t, emmc, parts[name]), bytes.Repeat([]byte{0xEE}, int(parts[name].Size))) {
			t.Errorf("%s was written", name)
		}
	}
	f, _ := os.Open(emmc)
	e, err := readEnv(f)
	f.Close()
	if err != nil {
		t.Fatal(err)
	}
	if e.get("blkdevparts") != "mmcblk0:"+strings.TrimPrefix(spec, "mmcblk1:") ||
		e.get("sys_bootargs") != " root=/dev/mmcblk0p9 rootfstype=ext4 fbcon=map:1" || e.get("sd_parts") != "mmcblk0:16K@512(env)" {
		t.Errorf("the eMMC's environment: %q", e)
	}
	idblock := region(t, card, parts["idblock"])
	if !bytes.HasPrefix(idblock, []byte(Installed)) || bytes.Count(idblock, []byte{0}) != len(idblock)-len(Installed) {
		t.Error("the card's idblock isn't replaced with the mark")
	}
	if strings.Join(*did, ",") != "still,data /userdata/touchstream,released" {
		t.Errorf("did %q", *did)
	}
	if len(said) == 0 || said[0] != "Installing… 0%" || !contains(said, "Installing… 100%") {
		t.Errorf("said %q", said)
	}
}

func TestKeepingTheCardLeavesItAbleToStartTheBox(t *testing.T) {
	card, emmc, parts := disks(t, 16<<20+minData)
	stubs(t)
	want := append([]byte{}, region(t, card, parts["idblock"])...)
	if err := ToEMMC(card, emmc, "x", Options{KeepCard: true}, func(string) {}); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(region(t, card, parts["idblock"]), want) {
		t.Error("the card's idblock changed")
	}
}

func TestTheCardsBootLoaderToo(t *testing.T) {
	card, emmc, parts := disks(t, 16<<20+minData)
	stubs(t)
	want := append([]byte{}, region(t, card, parts["uboot_a"])...)
	if err := ToEMMC(card, emmc, "x", Options{Bootloader: true}, func(string) {}); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(region(t, emmc, parts["uboot_a"]), want) {
		t.Error("the card's U-Boot didn't go")
	}
}

func TestNothingChangesOnADamagedCardOrTooSmallAnEMMC(t *testing.T) {
	card, emmc, _ := disks(t, 16<<20+minData)
	stubs(t)
	data, _ := os.ReadFile(card)
	data[100] ^= 0xFF // the environment's CRC no longer matches
	os.WriteFile(card, data, 0o600)
	if err := ToEMMC(card, emmc, "x", Options{}, func(string) {}); err == nil || !strings.Contains(err.Error(), "damaged") {
		t.Fatalf("damaged card: %v", err)
	}
	card, emmc, _ = disks(t, 12<<20)
	if err := ToEMMC(card, emmc, "x", Options{}, func(string) {}); err == nil || !strings.Contains(err.Error(), "too small") {
		t.Fatalf("small eMMC: %v", err)
	}
	if got, _ := os.ReadFile(emmc); !bytes.Equal(got, bytes.Repeat([]byte{0xEE}, len(got))) {
		t.Error("the eMMC was written")
	}
}

func TestTheInstallersCardBecomesOneForRecordings(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mmcblk1")
	f, _ := os.Create(path)
	f.Truncate(128 << 20)
	f.WriteAt([]byte("old environment"), 0)
	f.WriteAt([]byte(Installed), idblockAt)
	f.Close()
	os.WriteFile(path+"p1", nil, 0o600) // (the partition's device, as the kernel adds it)
	var formatted string
	oldReread, oldFormat := reread, formatExFAT
	reread = func(*os.File) error { return nil }
	formatExFAT = func(dev string) error { formatted = dev; return nil }
	t.Cleanup(func() { reread, formatExFAT = oldReread, oldFormat })

	did, err := Recordings(path)
	if err != nil || !did || formatted != path+"p1" {
		t.Fatalf("did %v, %v, formatted %q", did, err, formatted)
	}
	data, _ := os.ReadFile(path)
	p := data[446:462]
	if p[4] != 0x07 || binary.LittleEndian.Uint32(p[8:]) != 32768 || binary.LittleEndian.Uint32(p[12:]) != 128<<11-32768 ||
		data[510] != 0x55 || data[511] != 0xAA {
		t.Errorf("partition table: % x", p)
	}
	if bytes.Contains(data[:cardStart], []byte("old environment")) || bytes.Contains(data[:cardStart], []byte(Installed)) {
		t.Error("its start isn't cleared, or the mark's still there")
	}
	if did, _ := Recordings(path); did {
		t.Error("it was done again")
	}
}

func TestOtherCardsAreLeftAlone(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mmcblk1")
	os.WriteFile(path, bytes.Repeat([]byte("photos "), 1<<20), 0o600)
	if did, err := Recordings(path); did || err != nil {
		t.Fatalf("%v %v", did, err)
	}
	if did, err := Recordings(filepath.Join(t.TempDir(), "no-card")); did || err != nil {
		t.Fatalf("no card: %v %v", did, err)
	}
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}
