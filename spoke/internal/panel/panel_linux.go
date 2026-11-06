package panel

import (
	"encoding/binary"
	"errors"
	"os"
	"unsafe"

	"golang.org/x/sys/unix"
)

const fbiogetVscreeninfo = 0x4600 // FBIOGET_VSCREENINFO, from linux/fb.h

// query asks the framebuffer for its layout (FBIOGET_VSCREENINFO: struct fb_var_screeninfo, all
// 32-bit fields). The stride is the virtual width's worth of pixels.
func query(fb *os.File) (Format, error) {
	var v [160]byte
	if _, _, errno := unix.Syscall(unix.SYS_IOCTL, fb.Fd(), fbiogetVscreeninfo, uintptr(unsafe.Pointer(&v[0]))); errno != 0 {
		return Format{}, errno
	}
	u := func(at int) int { return int(binary.LittleEndian.Uint32(v[at:])) }
	f := Format{Width: u(0), Height: u(4), Bits: u(24),
		Red: Channel{u(32), u(36)}, Green: Channel{u(44), u(48)}, Blue: Channel{u(56), u(60)}}
	f.Stride = u(8) * f.Bits / 8
	if f.Width == 0 || f.Height == 0 || (f.Bits != 16 && f.Bits != 24 && f.Bits != 32) || f.Red.Length == 0 {
		return Format{}, errors.New("a framebuffer layout this doesn't draw")
	}
	return f, nil
}

// axisRange asks the touch driver for an axis's range (EVIOCGABS: struct input_absinfo, its
// value, minimum and maximum first), or takes it as the screen's own.
func axisRange(dev *os.File, code uintptr, size int) axis {
	var info [24]byte
	req := uintptr(0x80184540) + code // _IOR('E', 0x40 + code, 24 bytes)
	if _, _, errno := unix.Syscall(unix.SYS_IOCTL, dev.Fd(), req, uintptr(unsafe.Pointer(&info[0]))); errno != 0 {
		return axis{0, size - 1}
	}
	a := axis{int(int32(binary.LittleEndian.Uint32(info[4:]))), int(int32(binary.LittleEndian.Uint32(info[8:])))}
	if a.max <= a.min {
		return axis{0, size - 1}
	}
	return a
}
