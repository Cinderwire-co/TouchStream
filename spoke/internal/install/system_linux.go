package install

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"golang.org/x/sys/unix"
)

// makeDataPartition makes the eMMC's data partition afresh (an ext4 from at to its end: the old one
// was Luckfox's, at another place), with the spoke's settings from the card's (all but recordings).
func makeDataPartition(emmc string, at int64, settings string) error {
	out, err := exec.Command("losetup", "-f").Output()
	if err != nil {
		return fmt.Errorf("losetup: %w", err)
	}
	loop := strings.TrimSpace(string(out))
	if err := run("losetup", "-o", strconv.FormatInt(at, 10), loop, emmc); err != nil {
		return err
	}
	defer run("losetup", "-d", loop)
	if err := run("mkfs.ext4", "-F", "-q", "-L", "userdata", loop); err != nil {
		return err
	}
	dir, err := os.MkdirTemp("", "touchstream-emmc-")
	if err != nil {
		return err
	}
	defer os.Remove(dir)
	if err := run("mount", "-t", "ext4", loop, dir); err != nil {
		return err
	}
	defer run("umount", dir)
	dst := filepath.Join(dir, "touchstream")
	if err := os.MkdirAll(dst, 0o755); err != nil {
		return err
	}
	entries, err := os.ReadDir(settings)
	if err != nil {
		return err
	}
	for _, d := range entries {
		if d.Name() != "recordings" {
			if err := run("cp", "-a", filepath.Join(settings, d.Name()), dst); err != nil {
				return err
			}
		}
	}
	return os.WriteFile(filepath.Join(dst, ".grown"), nil, 0o644) // (made at its full size already)
}

func rereadPartitions(f *os.File) error {
	return unix.IoctlSetInt(int(f.Fd()), unix.BLKRRPART, 0)
}

// stillRoot keeps the system the box is running from still while it's copied: read-only until the
// returned func (sshd may be writing its keys, at the first start: so a few tries).
func stillRoot() (func(), error) {
	_ = run("sync")
	var err error
	for i := 0; i < 10; i++ {
		if err = run("mount", "-o", "remount,ro", "/"); err == nil {
			return func() { _ = run("mount", "-o", "remount,rw", "/") }, nil
		}
		time.Sleep(time.Second)
	}
	return nil, fmt.Errorf("the system's busy: %w", err)
}

func run(name string, args ...string) error {
	if out, err := exec.Command(name, args...).CombinedOutput(); err != nil {
		return fmt.Errorf("%s: %v %s", name, err, strings.TrimSpace(string(out)))
	}
	return nil
}
