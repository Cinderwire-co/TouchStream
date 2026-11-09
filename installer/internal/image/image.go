// Package image writes the spoke's image to a microSD card: filling in that spoke's settings as
// it goes (so the box is paired with the hub from its first boot), then reading the card back to
// check every byte.
//
// The image keeps a 64 KiB block for the settings, 64 KiB-aligned in its first 64 MiB, that starts
// with Magic. The installer replaces the block with Magic, the settings as JSON, and zeros; the
// spoke reads it on its first boot. An image without the block is written as it is: the box then
// shows up under Add spoke in the dashboard, to adopt.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package image

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/klauspost/compress/zstd"
	"github.com/ulikunitz/xz"
)

const (
	Magic     = "TOUCHSTREAM-PROVISION-V1\n"
	BlockSize = 64 << 10 // the settings block, aligned to its own size
	searchIn  = 64 << 20 // within the image's first 64 MiB
	chunk     = 4 << 20  // written 4 MiB at a time: whole sectors, and whole blocks
	sector    = 4096     // the last write is padded to this
)

// Provision is what a spoke needs to find its hub. With no hub (Spoke and Token empty) only the
// name is set.
type Provision struct {
	HubURL  string `json:"hub_url,omitempty"`
	SpokeID string `json:"spoke_id,omitempty"`
	Token   string `json:"token,omitempty"`
	Name    string `json:"name"`
}

// Block is the settings block for p: Magic, its JSON and a newline, then zeros.
func Block(p Provision) ([]byte, error) {
	data, err := json.Marshal(p)
	if err != nil {
		return nil, err
	}
	if len(Magic)+len(data)+1 > BlockSize {
		return nil, errors.New("the spoke's settings don't fit their block")
	}
	block := make([]byte, BlockSize)
	copy(block, Magic)
	copy(block[len(Magic):], append(data, '\n'))
	return block, nil
}

// Parse reads a settings block back (the spoke does the same on its first boot).
func Parse(block []byte) (*Provision, error) {
	if !bytes.HasPrefix(block, []byte(Magic)) {
		return nil, errors.New("not a settings block")
	}
	line, _, _ := bytes.Cut(block[len(Magic):], []byte{'\n'})
	var p Provision
	if err := json.Unmarshal(line, &p); err != nil {
		return nil, err
	}
	return &p, nil
}

// Unpack makes a plain image from path (decompressing .zst, .xz or .gz) at dst, once: writing several
// cards then reads it straight off the disk. It returns the image's size. Releases have a .zst for
// the installer: Go unpacks it about fifteen times faster than the .xz (seconds, not minutes, for
// the spoke's 1.4 GB), and it's no bigger.
func Unpack(path, dst string) (int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	var src io.Reader = f
	switch {
	case strings.HasSuffix(path, ".gz"):
		z, err := gzip.NewReader(f)
		if err != nil {
			return 0, err
		}
		src = z
	case strings.HasSuffix(path, ".zst"):
		// (the release's is made with zstd --long=27: a window of 128 MiB)
		z, err := zstd.NewReader(f, zstd.WithDecoderMaxWindow(1<<28))
		if err != nil {
			return 0, err
		}
		defer z.Close()
		src = z
	case strings.HasSuffix(path, ".xz"):
		z, err := xz.NewReader(f)
		if err != nil {
			return 0, err
		}
		src = z
	case path == dst:
		info, err := f.Stat()
		if err != nil {
			return 0, err
		}
		return info.Size(), nil
	}
	out, err := os.Create(dst + ".part")
	if err != nil {
		return 0, err
	}
	n, err := io.Copy(out, src)
	if cerr := out.Close(); err == nil {
		err = cerr
	}
	if err == nil {
		err = os.Rename(dst+".part", dst)
	}
	if err != nil {
		os.Remove(dst + ".part")
		return 0, fmt.Errorf("unpacking the image: %w", err)
	}
	return n, nil
}

// Result is what Write wrote: how much (padded to whole sectors), its SHA-256, and whether the
// image had a settings block to fill in.
type Result struct {
	Written     int64
	Sum         [sha256.Size]byte
	Provisioned bool
}

// HasBlock reports whether the image has a settings block for Write to fill in. Without one (an
// image not made for the installer, such as Luckfox's own), the box can't be paired from its card.
func HasBlock(src io.Reader) (bool, error) {
	block := make([]byte, len(Magic))
	for at := 0; at < searchIn; at += BlockSize {
		if _, err := io.ReadFull(src, block); err == io.EOF || err == io.ErrUnexpectedEOF {
			return false, nil
		} else if err != nil {
			return false, err
		}
		if bytes.Equal(block, []byte(Magic)) {
			return true, nil
		}
		if _, err := io.CopyN(io.Discard, src, BlockSize-int64(len(Magic))); err == io.EOF {
			return false, nil
		} else if err != nil {
			return false, err
		}
	}
	return false, nil
}

// Padded is how much Write writes for an image of size bytes: whole sectors.
func Padded(size int64) int64 { return (size + sector - 1) / sector * sector }

// Write copies the image to dst, filling in p's settings block, with progress as it goes. Its
// first piece (with the partition table) goes on last: until then the card has no partitions, so
// neither macOS nor Windows mounts one and writes to it while the rest is going on.
func Write(dst io.WriterAt, src io.Reader, p *Provision, progress func(done int64)) (Result, error) {
	var block []byte
	if p != nil {
		var err error
		if block, err = Block(*p); err != nil {
			return Result{}, err
		}
	}
	var res Result
	hash := sha256.New()
	buf := make([]byte, chunk)
	var first []byte
	for {
		n, err := io.ReadFull(src, buf)
		if n > 0 {
			piece := buf[:n]
			if pad := Padded(int64(n)); pad != int64(n) { // the last piece: whole sectors
				piece = buf[:pad]
				clear(piece[n:])
			}
			if block != nil && res.Written < searchIn {
				for at := 0; at+BlockSize <= len(piece); at += BlockSize {
					if bytes.HasPrefix(piece[at:], []byte(Magic)) {
						copy(piece[at:], block)
						res.Provisioned = true
					}
				}
			}
			if res.Written == 0 {
				first = append([]byte{}, piece...)
				if _, werr := dst.WriteAt(make([]byte, len(piece)), 0); werr != nil { // no partitions, yet
					return res, fmt.Errorf("writing the card: %w", werr)
				}
			} else if _, werr := dst.WriteAt(piece, res.Written); werr != nil {
				return res, fmt.Errorf("writing the card: %w", werr)
			}
			hash.Write(piece)
			res.Written += int64(len(piece))
			if progress != nil {
				progress(res.Written)
			}
		}
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			break
		}
		if err != nil {
			return res, fmt.Errorf("reading the image: %w", err)
		}
	}
	if first != nil {
		if _, err := dst.WriteAt(first, 0); err != nil {
			return res, fmt.Errorf("writing the card: %w", err)
		}
	}
	copy(res.Sum[:], hash.Sum(nil))
	return res, nil
}

// Verify reads n bytes back and checks they're what was written.
func Verify(src io.Reader, n int64, sum [sha256.Size]byte, progress func(done int64)) error {
	hash := sha256.New()
	buf := make([]byte, chunk)
	var done int64
	for done < n {
		want := min(int64(len(buf)), n-done)
		got, err := io.ReadFull(src, buf[:want])
		hash.Write(buf[:got])
		done += int64(got)
		if progress != nil {
			progress(done)
		}
		if err != nil {
			return fmt.Errorf("reading the card back: %w", err)
		}
	}
	if !bytes.Equal(hash.Sum(nil), sum[:]) {
		return errors.New("the card didn't read back what was written: try another card")
	}
	return nil
}
