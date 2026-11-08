package image

import (
	"bytes"
	"compress/gzip"
	"os"
	"path/filepath"
	"testing"
)

// card is a card in memory, recording the order of its writes.
type card struct {
	data   []byte
	starts []int64
}

func (c *card) WriteAt(b []byte, at int64) (int, error) {
	if need := at + int64(len(b)); need > int64(len(c.data)) {
		c.data = append(c.data, make([]byte, need-int64(len(c.data)))...)
	}
	c.starts = append(c.starts, at)
	return copy(c.data[at:], b), nil
}

func (c *card) Len() int      { return len(c.data) }
func (c *card) Bytes() []byte { return c.data }

// fake is an image of size bytes with a settings block at offset at (or none if at < 0).
func fake(size, at int) []byte {
	img := bytes.Repeat([]byte{0xA5}, size)
	if at >= 0 {
		copy(img[at:], Magic)
		clear(img[at+len(Magic) : at+BlockSize])
	}
	return img
}

func TestTheSpokesSettingsGoInTheirBlock(t *testing.T) {
	img := fake(9<<20+1000, 5<<20) // the block in the second chunk; the last piece isn't whole sectors
	p := &Provision{HubURL: "http://192.168.1.20:8080", SpokeID: "ab12cd34", Token: "secret", Name: "Auditorium"}
	var card card
	res, err := Write(&card, bytes.NewReader(img), p, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !res.Provisioned || res.Written != Padded(int64(len(img))) || int64(card.Len()) != res.Written {
		t.Fatalf("%+v, card %d", res, card.Len())
	}
	got, err := Parse(card.Bytes()[5<<20:])
	if err != nil || *got != *p {
		t.Fatalf("read back %+v, %v", got, err)
	}
	if !bytes.Equal(card.Bytes()[:5<<20], img[:5<<20]) || !bytes.Equal(card.Bytes()[6<<20:len(img)], img[6<<20:]) {
		t.Fatal("the rest of the image changed")
	}
	if err := Verify(bytes.NewReader(card.Bytes()), res.Written, res.Sum, nil); err != nil {
		t.Fatal(err)
	}
	if last := card.starts[len(card.starts)-1]; last != 0 || card.starts[0] != 0 {
		t.Fatalf("the partition table should be blank first and written last: %v", card.starts)
	}
}

func TestACardThatReadsBackWrongFails(t *testing.T) {
	img := fake(1<<20, -1)
	var card card
	res, _ := Write(&card, bytes.NewReader(img), nil, nil)
	bad := card.Bytes()
	bad[12345] ^= 1
	if Verify(bytes.NewReader(bad), res.Written, res.Sum, nil) == nil {
		t.Fatal("a flipped bit passed")
	}
	if Verify(bytes.NewReader(bad[:1000]), res.Written, res.Sum, nil) == nil {
		t.Fatal("a short card passed")
	}
}

func TestAnImageWithoutABlockIsWrittenAsItIs(t *testing.T) {
	img := fake(2<<20, -1)
	var card card
	res, err := Write(&card, bytes.NewReader(img), &Provision{Name: "x"}, nil)
	if err != nil || res.Provisioned || !bytes.Equal(card.Bytes(), img) {
		t.Fatalf("%+v %v", res, err)
	}
}

func TestFindingTheBlock(t *testing.T) {
	for name, c := range map[string]struct {
		img  []byte
		want bool
	}{
		"at 2880K":   {fake(4<<20, 2880<<10), true},
		"none":       {fake(4<<20, -1), false},
		"misaligned": {fake(4<<20, 3<<10), false},
		"tiny image": {[]byte("TOUCH"), false},
	} {
		if got, err := HasBlock(bytes.NewReader(c.img)); err != nil || got != c.want {
			t.Errorf("%s: %v, %v", name, got, err)
		}
	}
}

func TestUnpackingAGzippedImage(t *testing.T) {
	dir := t.TempDir()
	img := fake(3<<20, 0)
	var z bytes.Buffer
	w := gzip.NewWriter(&z)
	w.Write(img)
	w.Close()
	src := filepath.Join(dir, "spoke.img.gz")
	os.WriteFile(src, z.Bytes(), 0o644)
	n, err := Unpack(src, filepath.Join(dir, "spoke.img"))
	if err != nil || n != int64(len(img)) {
		t.Fatalf("%d %v", n, err)
	}
	if out, _ := os.ReadFile(filepath.Join(dir, "spoke.img")); !bytes.Equal(out, img) {
		t.Fatal("unpacked differently")
	}
}
