package panel

import (
	"context"
	"encoding/binary"
	"os"
	"path/filepath"
	"strings"
)

// The PicoKVM's BOOT button, as Linux has it: a key on adc-keys. (Its RESET button restarts the
// box itself: no software sees it.)
const (
	BootButton = "adc-keys"
	KeyBoot    = 115 // KEY_VOLUMEUP
)

// Key is a button: Press is told each time it goes down (true) and comes up (false).
type Key struct {
	Path  string
	Code  uint16
	Press func(down bool)
}

func (k *Key) Run(ctx context.Context) error {
	dev, err := os.Open(k.Path)
	if err != nil {
		return err
	}
	go func() { <-ctx.Done(); dev.Close() }() // the read below returns then
	buf := make([]byte, eventSize*16)
	for {
		n, err := dev.Read(buf)
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return err
		}
		k.feed(buf[:n-n%eventSize])
	}
}

func (k *Key) feed(events []byte) {
	for at := 0; at+eventSize <= len(events); at += eventSize {
		e := events[at+2*longSize:]
		kind, code := binary.LittleEndian.Uint16(e), binary.LittleEndian.Uint16(e[2:])
		value := int32(binary.LittleEndian.Uint32(e[4:]))
		if kind == evKey && code == k.Code && value != 2 { // (2: held, repeating)
			k.Press(value == 1)
		}
	}
}

// Input is the input device (/dev/input/eventN) Linux calls name, or "".
func Input(name string) string {
	found, _ := filepath.Glob("/sys/class/input/event*/device/name")
	for _, path := range found {
		if data, err := os.ReadFile(path); err == nil && strings.TrimSpace(string(data)) == name {
			return "/dev/input/" + filepath.Base(filepath.Dir(filepath.Dir(path)))
		}
	}
	return ""
}
