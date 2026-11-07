// Package install puts the spoke on the PicoKVM itself, its eMMC, from the installer's card it
// started from (the boot ROM tries the card first), and then makes that card one for recordings.
//
//  1. ToEMMC, started from the card: the card's partitions go onto the eMMC at the same places,
//     each checked; the eMMC's data partition is made afresh, with the spoke's settings (its
//     pairing) from the card's; then the eMMC's environment (its partition table, for mmcblk0),
//     last: until then, a power cut leaves the card as the one that starts, to start over. Then the
//     card's idblock is replaced with Installed, so the box starts from the eMMC, and it restarts.
//  2. Recordings, started from the eMMC: a card marked Installed becomes one exFAT partition,
//     "TouchStream". Any other card is left as it is.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package install

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"hash/crc32"
	"io"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const (
	Card, EMMC = "/dev/mmcblk1", "/dev/mmcblk0"
	Installed  = "TOUCHSTREAM-INSTALLED\n" // where the card's idblock was, once the spoke's on the eMMC

	envSize   = 32 << 10
	idblockAt = 32 << 10
	cardStart = 16 << 20  // where the recordings partition starts: an SD card's erase blocks line up there
	minData   = 256 << 20 // the least room for the eMMC's data partition
)

// What goes from the card to the eMMC: all but the environment (rewritten for the eMMC), and the
// data partition (made afresh). The boot loader's in Bootloader: see ToEMMC.
var (
	system     = []string{"misc", "security", "boot_a", "boot_b", "system_a", "system_b"}
	Bootloader = []string{"idblock", "uboot_a", "uboot_b"}
)

// The parts of it that are the box's own tools, replaced in tests.
var (
	makeData    = makeDataPartition // the eMMC's data partition, with the spoke's settings
	reread      = rereadPartitions  // the kernel reads the card's partition table again
	holdStill   = stillRoot         // the system it's running from, read-only while it's copied
	formatExFAT = func(dev string) error { return run("mkfs.exfat", "-n", "TouchStream", dev) }
)

// Options are for trying it out, and for boxes it's not been tried on.
type Options struct {
	Bootloader bool // the card's idblock and U-Boot go too (else the eMMC's own stay: made to start from it)
	KeepCard   bool // the card still starts the box (to try the eMMC with the card taken out)
}

// ToEMMC copies the card's system onto the eMMC, with settings (the spoke's folder on the card's
// data partition, less its recordings) on the eMMC's. say is told how it's going, in a few words
// for the box's screen.
func ToEMMC(card, emmc, settings string, o Options, say func(string)) error {
	c, err := os.OpenFile(card, os.O_RDWR, 0)
	if err != nil {
		return err
	}
	defer c.Close()
	e, err := os.OpenFile(emmc, os.O_RDWR, 0)
	if err != nil {
		return err
	}
	defer e.Close()
	env, err := readEnv(c)
	if err != nil {
		return err
	}
	parts, err := layout(env.get("blkdevparts"), size(c))
	if err != nil {
		return err
	}
	emmcEnv, err := forEMMC(env)
	if err != nil {
		return err
	}
	data, ok := parts["userdata"]
	if !ok {
		return errors.New("the card has no data partition")
	}
	if size(e) < data.At+minData {
		return fmt.Errorf("the box's storage is too small (%d MB)", size(e)>>20)
	}
	names := append([]string{}, system...)
	if o.Bootloader {
		names = append(names, Bootloader...)
	}
	release, err := holdStill()
	if err != nil {
		return err
	}
	defer release() // (the box restarts once it's done; if it didn't finish, it carries on from the card)
	var total, done int64
	for _, name := range names {
		p, ok := parts[name]
		if !ok {
			return fmt.Errorf("the card has no %s partition", name)
		}
		total += p.Size
	}
	said := -1
	progress := func(n int64) {
		done += n
		if pc := int(done * 100 / total); pc != said {
			said = pc
			say(fmt.Sprintf("Installing… %d%%", pc))
		}
	}
	progress(0)
	for _, name := range names {
		if err := copyPart(c, e, parts[name], progress); err != nil {
			return fmt.Errorf("copying %s: %w", name, err)
		}
	}
	if err := e.Sync(); err != nil {
		return err
	}
	say("Checking…")
	for _, name := range names {
		if same, err := samePart(c, e, parts[name]); err != nil || !same {
			return fmt.Errorf("%s didn't copy right (%v): try another card", name, err)
		}
	}
	say("Settings…")
	if err := makeData(emmc, data.At, settings); err != nil {
		return fmt.Errorf("the box's data partition: %w", err)
	}
	block, err := emmcEnv.bytes()
	if err != nil {
		return err
	}
	if _, err := e.WriteAt(block, 0); err != nil {
		return err
	}
	if err := e.Sync(); err != nil {
		return err
	}
	if o.KeepCard {
		return nil
	}
	// The card won't start the box again: the eMMC does, and it makes the card one for recordings
	idblock := make([]byte, parts["idblock"].Size)
	copy(idblock, Installed)
	if _, err := c.WriteAt(idblock, idblockAt); err != nil {
		return err
	}
	return c.Sync()
}

// Recordings makes a card marked Installed one for recordings: one exFAT partition, from 16 MiB.
// Any other card is left as it is. It says whether it did.
func Recordings(card string) (bool, error) {
	f, err := os.OpenFile(card, os.O_RDWR, 0)
	if err != nil {
		return false, nil // (no card in)
	}
	defer f.Close()
	mark := make([]byte, len(Installed))
	if _, err := f.ReadAt(mark, idblockAt); err != nil || string(mark) != Installed {
		return false, nil
	}
	n := size(f)
	if n < cardStart+(64<<20) || n/512-cardStart/512 > 0xFFFFFFFF {
		return false, fmt.Errorf("the card's size (%d MB) won't do for recordings", n>>20)
	}
	// Its start cleared (the old environment, boot loader and boot partitions), but for the mark, so
	// that it's done again if it doesn't finish; then the partition table, and the partition
	clear := make([]byte, cardStart)
	copy(clear[idblockAt:], Installed)
	if _, err := f.WriteAt(clear, 0); err != nil {
		return false, err
	}
	if _, err := f.WriteAt(mbr(cardStart/512, n/512-cardStart/512), 0); err != nil {
		return false, err
	}
	if err := f.Sync(); err != nil {
		return false, err
	}
	if err := reread(f); err != nil {
		return false, err
	}
	part := card + "p1"
	for i := 0; i < 50; i++ { // (the kernel adds its device)
		if _, err := os.Stat(part); err == nil {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if err := formatExFAT(part); err != nil {
		return false, err
	}
	if _, err := f.WriteAt(make([]byte, len(Installed)), idblockAt); err != nil {
		return false, err
	}
	return true, f.Sync()
}

// mbr is a partition table with one partition: exFAT, from sector start for count sectors.
func mbr(start, count int64) []byte {
	b := make([]byte, 512)
	p := b[446:]
	p[1], p[2], p[3] = 0xFE, 0xFF, 0xFF // its first and last sectors, as cylinders, heads and sectors: past them
	p[4] = 0x07                         // exFAT (or NTFS)
	p[5], p[6], p[7] = 0xFE, 0xFF, 0xFF
	binary.LittleEndian.PutUint32(p[8:], uint32(start))
	binary.LittleEndian.PutUint32(p[12:], uint32(count))
	b[510], b[511] = 0x55, 0xAA
	return b
}

// ---- The environment: U-Boot's, at the start of the card (its CRC, then name=value, each ended by a zero)

type env []string

func readEnv(r io.ReaderAt) (env, error) {
	b := make([]byte, envSize)
	if _, err := r.ReadAt(b, 0); err != nil {
		return nil, err
	}
	if crc32.ChecksumIEEE(b[4:]) != binary.LittleEndian.Uint32(b) {
		return nil, errors.New("the card's environment (its partition table) is damaged")
	}
	var e env
	for _, kv := range bytes.Split(b[4:], []byte{0}) {
		if len(kv) == 0 {
			break
		}
		e = append(e, string(kv))
	}
	return e, nil
}

func (e env) get(name string) string {
	for _, kv := range e {
		if k, v, _ := strings.Cut(kv, "="); k == name {
			return v
		}
	}
	return ""
}

func (e env) set(name, value string) env {
	out := append(env{}, e...)
	for i, kv := range out {
		if k, _, _ := strings.Cut(kv, "="); k == name {
			out[i] = name + "=" + value
			return out
		}
	}
	return append(out, name+"="+value)
}

func (e env) bytes() ([]byte, error) {
	b := make([]byte, envSize)
	data := []byte(strings.Join(e, "\x00") + "\x00\x00")
	if len(data) > envSize-4 {
		return nil, errors.New("the environment doesn't fit")
	}
	copy(b[4:], data)
	binary.LittleEndian.PutUint32(b, crc32.ChecksumIEEE(b[4:]))
	return b, nil
}

// forEMMC is the card's environment for the eMMC: its partitions, and the system's root, on mmcblk0.
func forEMMC(e env) (env, error) {
	spec, args := e.get("blkdevparts"), e.get("sys_bootargs")
	if !strings.HasPrefix(spec, "mmcblk1:") || !strings.Contains(args, "root=/dev/mmcblk1p") {
		return nil, errors.New("the card's environment isn't a card's")
	}
	e = e.set("blkdevparts", "mmcblk0:"+strings.TrimPrefix(spec, "mmcblk1:"))
	return e.set("sys_bootargs", strings.Replace(args, "root=/dev/mmcblk1p", "root=/dev/mmcblk0p", 1)), nil
}

// ---- The partitions, as blkdevparts has them: mmcblk1:32K(env),512K@32K(idblock),...,-(userdata)

type part struct{ At, Size int64 }

var partSpec = regexp.MustCompile(`^(-|\d+[KMG])(?:@(\d+[KMG]))?\((\w+)\)$`)

func layout(spec string, diskSize int64) (map[string]part, error) {
	_, list, ok := strings.Cut(spec, ":")
	if !ok {
		return nil, fmt.Errorf("no partitions in %q", spec)
	}
	out, at := map[string]part{}, int64(0)
	for _, item := range strings.Split(list, ",") {
		m := partSpec.FindStringSubmatch(item)
		if m == nil {
			return nil, fmt.Errorf("can't read the partition %q", item)
		}
		if m[2] != "" {
			at = amount(m[2])
		}
		n := diskSize - at
		if m[1] != "-" {
			n = amount(m[1])
		}
		out[m[3]] = part{at, n}
		at += n
	}
	return out, nil
}

func amount(s string) int64 {
	n, _ := strconv.ParseInt(s[:len(s)-1], 10, 64)
	return n << map[byte]uint{'K': 10, 'M': 20, 'G': 30}[s[len(s)-1]]
}

// ---- Copying, and checking

func size(f *os.File) int64 {
	n, _ := f.Seek(0, io.SeekEnd)
	return n
}

func copyPart(src io.ReaderAt, dst io.WriterAt, p part, progress func(int64)) error {
	buf := make([]byte, 4<<20)
	for off := int64(0); off < p.Size; off += int64(len(buf)) {
		chunk := buf[:min(int64(len(buf)), p.Size-off)]
		if _, err := src.ReadAt(chunk, p.At+off); err != nil {
			return err
		}
		if _, err := dst.WriteAt(chunk, p.At+off); err != nil {
			return err
		}
		progress(int64(len(chunk)))
	}
	return nil
}

func samePart(a, b io.ReaderAt, p part) (bool, error) {
	x, y := make([]byte, 4<<20), make([]byte, 4<<20)
	for off := int64(0); off < p.Size; off += int64(len(x)) {
		n := min(int64(len(x)), p.Size-off)
		if _, err := a.ReadAt(x[:n], p.At+off); err != nil {
			return false, err
		}
		if _, err := b.ReadAt(y[:n], p.At+off); err != nil {
			return false, err
		}
		if !bytes.Equal(x[:n], y[:n]) {
			return false, nil
		}
	}
	return true, nil
}
