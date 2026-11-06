package cards

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"
)

// diskutil answers in property lists; plutil turns them into JSON.
func diskutil(args ...string) (map[string]any, error) {
	out, err := exec.Command("diskutil", append(args[:1:1], append([]string{"-plist"}, args[1:]...)...)...).Output()
	if err != nil {
		return nil, fmt.Errorf("diskutil %s: %w", strings.Join(args, " "), err)
	}
	conv := exec.Command("plutil", "-convert", "json", "-o", "-", "-")
	conv.Stdin = bytes.NewReader(out)
	js, err := conv.Output()
	if err != nil {
		return nil, err
	}
	var m map[string]any
	return m, json.Unmarshal(js, &m)
}

// List finds the cards in this Mac (with virtual, disk images too).
func List(virtual bool) ([]Card, error) {
	all, err := diskutil("list")
	if err != nil {
		return nil, err
	}
	system := map[string]bool{}
	if root, err := diskutil("info", "/"); err == nil {
		system[str(root["ParentWholeDisk"])] = true
		for _, store := range list(root["APFSPhysicalStores"]) { // the disk under the system's APFS container
			if m, ok := store.(map[string]any); ok {
				system[wholeDisk(str(m["APFSPhysicalStore"]))] = true
			}
		}
	}
	var disks []Disk
	for _, whole := range list(all["WholeDisks"]) {
		id := str(whole)
		info, err := diskutil("info", id)
		if err != nil {
			continue
		}
		image := str(info["BusProtocol"]) == "Disk Image"
		d := Disk{Card: Card{ID: id, Device: "/dev/r" + id, Name: str(info["MediaName"]), Bus: str(info["BusProtocol"]),
			Size: num(info["TotalSize"]), ReadOnly: info["WritableMedia"] == false, Virtual: image},
			Internal: info["Internal"] == true, Removable: info["RemovableMedia"] == true || info["Ejectable"] == true,
			System:      system[id] || info["OSInternalMedia"] == true,
			Synthesized: info["VirtualOrPhysical"] == "Virtual" && !image}
		d.Volumes = volumes(all, id)
		disks = append(disks, d)
	}
	return Offer(disks, virtual), nil
}

// volumes is the names of what's on a disk now: its partitions', and those of any APFS container
// built on one of them (where an APFS-formatted card's volumes are).
func volumes(all map[string]any, id string) []string {
	var out []string
	for _, entry := range list(all["AllDisksAndPartitions"]) {
		m, _ := entry.(map[string]any)
		on := str(m["DeviceIdentifier"]) == id
		for _, store := range list(m["APFSPhysicalStores"]) {
			if s, _ := store.(map[string]any); wholeDisk(str(s["DeviceIdentifier"])) == id {
				on = true
			}
		}
		if !on {
			continue
		}
		for _, key := range []string{"Partitions", "APFSVolumes"} {
			for _, part := range list(m[key]) {
				if p, _ := part.(map[string]any); str(p["VolumeName"]) != "" {
					out = append(out, str(p["VolumeName"]))
				}
			}
		}
	}
	return out
}

// Unmount takes a card's volumes out of use, so it can be written (no admin rights needed).
func Unmount(c Card) error {
	out, err := exec.Command("diskutil", "unmountDisk", "/dev/"+c.ID).CombinedOutput()
	if err != nil {
		return fmt.Errorf("couldn't get the card out of use (is a file on it open?): %s", strings.TrimSpace(string(out)))
	}
	return nil
}

// Open opens a card to write (as admin, or as anyone for a disk image of their own). macOS can
// mount a card's volumes again at any moment (closing a disk opened to write is enough), so a busy
// card is unmounted again and retried.
func Open(c Card) (Target, error) {
	for try := 0; ; try++ {
		f, err := os.OpenFile(c.Device, os.O_RDWR, 0)
		switch {
		case err == nil:
			return f, nil
		case errors.Is(err, os.ErrNotExist):
			return nil, ErrGone
		case errors.Is(err, syscall.EBUSY) && try < 4:
			_ = Unmount(c)
			time.Sleep(500 * time.Millisecond)
		case errors.Is(err, syscall.EBUSY):
			return nil, ErrBusy
		default:
			return nil, err
		}
	}
}

// Writable reports whether this program can write the card as it is, without admin rights.
// It asks without opening the card, which would have macOS mount it again.
func Writable(c Card) bool { return syscall.Access(c.Device, 2 /* W_OK */) == nil }

// Eject finishes with a card, so it can be taken out.
func Eject(c Card) error {
	if out, err := exec.Command("diskutil", "eject", "/dev/"+c.ID).CombinedOutput(); err != nil {
		return fmt.Errorf("eject: %s", strings.TrimSpace(string(out)))
	}
	return nil
}

func wholeDisk(id string) string { // disk3s2 -> disk3
	if i := strings.LastIndex(id, "s"); i > len("disk") {
		return id[:i]
	}
	return id
}

func str(v any) string { s, _ := v.(string); return s }
func num(v any) int64  { f, _ := v.(float64); return int64(f) }
func list(v any) []any { l, _ := v.([]any); return l }
