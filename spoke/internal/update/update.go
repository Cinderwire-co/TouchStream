// Package update puts a newer spoke on the box, from its hub: the hub offers the spoke of its own
// version (from TouchStream's GitHub release) when the spoke's idle, and the spoke downloads it,
// checks it was signed by TouchStream's release key, and unpacks it beside the one it's running.
//
// The box's start script (touchstream-start) runs $data/app/current's version, or the one built into
// the box's system if there's none. A new version starts on trial: it has to reach the hub before it
// has been started three times, or the start script goes back to the version before it and notes the
// one that failed (which the spoke reports, so the hub doesn't offer it again by itself).
//
//	$data/app/0.3.0/touchstream-spoke   (and touchstream-capture)
//	$data/app/0.3.0/.trying             there until it has reached its hub
//	$data/app/current, previous         a version each ("" or no file: the box's own)
//	$data/app/failed                    the version that didn't start, if one didn't
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package update

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// PublicKey is TouchStream's release key (ed25519, base64). Release builds of the spoke are signed
// with its private half, which is never in this repository. Test builds set their own with
// -ldflags "-X github.com/Cinderwire-co/TouchStream/spoke/internal/update.PublicKey=...".
var PublicKey = "KRBR1MSv7Ag5zDXZNYsaytk5xy80MZrJE4Ah0cxS2Us="

// Offer is a newer spoke the hub has for this one.
type Offer struct {
	Version   string `json:"version"`
	URL       string `json:"url"` // on the hub, from its address: /api/spoke/update/0.3.0
	SHA256    string `json:"sha256"`
	Size      int64  `json:"size"`
	Signature string `json:"signature"` // base64 ed25519 over Message(version, sha256)
}

// Files is what a spoke release holds: nothing else is unpacked from it.
var Files = map[string]bool{"touchstream-spoke": true, "touchstream-capture": true}

var versionRE = regexp.MustCompile(`^\d+\.\d+\.\d+$`)

// Release says whether v is a released version (0.3.0), not a developer's build ("0.1.0-dev").
func Release(v string) bool { return versionRE.MatchString(v) }

// Message is what's signed for a release: its version and its file's SHA-256, so neither can be swapped.
func Message(version, sha string) []byte {
	return []byte(fmt.Sprintf("touchstream-spoke %s %s", version, strings.ToLower(sha)))
}

// Verify checks an offer's signature against the release key.
func Verify(o Offer) error {
	key, err := base64.StdEncoding.DecodeString(PublicKey)
	if err != nil || len(key) != ed25519.PublicKeySize {
		return errors.New("this spoke has no release key to check updates with")
	}
	sig, err := base64.StdEncoding.DecodeString(o.Signature)
	if err != nil || !ed25519.Verify(key, Message(o.Version, o.SHA256), sig) {
		return errors.New("the update isn't signed by TouchStream's release key")
	}
	return nil
}

// Dir is where versions are kept.
func Dir(data string) string { return filepath.Join(data, "app") }

// Install downloads the offer from the hub, checks it and unpacks it, then makes it the version the
// box starts next (on trial). progress gets the share downloaded, 0 to 1. The caller restarts the spoke.
func Install(ctx context.Context, o Offer, data, hubURL string, auth map[string]string, progress func(float64)) error {
	if !Release(o.Version) {
		return fmt.Errorf("%q isn't a release", o.Version)
	}
	if err := Verify(o); err != nil {
		return err
	}
	app := Dir(data)
	if err := os.MkdirAll(app, 0o755); err != nil {
		return err
	}
	download := filepath.Join(app, ".download")
	defer os.Remove(download)
	if err := fetch(ctx, strings.TrimRight(hubURL, "/")+o.URL, auth, download, o, progress); err != nil {
		return err
	}
	dir := filepath.Join(app, o.Version)
	staging := dir + ".new"
	_ = os.RemoveAll(staging)
	if err := unpack(download, staging); err != nil {
		_ = os.RemoveAll(staging)
		return err
	}
	// The new spoke has to run here: a build for the wrong box fails now, not at the next start
	out, err := exec.CommandContext(ctx, filepath.Join(staging, "touchstream-spoke"), "version").Output()
	if err != nil || strings.TrimSpace(string(out)) != o.Version {
		_ = os.RemoveAll(staging)
		return fmt.Errorf("the new spoke doesn't run on this box (%v: %q)", err, strings.TrimSpace(string(out)))
	}
	if err := os.WriteFile(filepath.Join(staging, ".trying"), []byte("0\n"), 0o644); err != nil {
		return err
	}
	_ = os.RemoveAll(dir)
	if err := os.Rename(staging, dir); err != nil {
		return err
	}
	// previous <- current, current <- the new one
	current := Current(data)
	if current != o.Version {
		if err := write(filepath.Join(app, "previous"), current); err != nil {
			return err
		}
	}
	if err := write(filepath.Join(app, "current"), o.Version); err != nil {
		return err
	}
	_ = os.Remove(filepath.Join(app, "failed"))
	prune(app, o.Version, current)
	return nil
}

func fetch(ctx context.Context, url string, auth map[string]string, to string, o Offer, progress func(float64)) error {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Minute)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	for k, v := range auth {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("the hub answered %s for the update", resp.Status)
	}
	f, err := os.Create(to)
	if err != nil {
		return err
	}
	defer f.Close()
	sum := sha256.New()
	var got int64
	buf := make([]byte, 64<<10)
	for {
		n, rerr := resp.Body.Read(buf)
		if n > 0 {
			got += int64(n)
			if o.Size > 0 && got > o.Size {
				return errors.New("the update is bigger than the hub said")
			}
			sum.Write(buf[:n])
			if _, err := f.Write(buf[:n]); err != nil {
				return err
			}
			if progress != nil && o.Size > 0 {
				progress(float64(got) / float64(o.Size))
			}
		}
		if rerr == io.EOF {
			break
		}
		if rerr != nil {
			return rerr
		}
	}
	if hex.EncodeToString(sum.Sum(nil)) != strings.ToLower(o.SHA256) {
		return errors.New("the update didn't arrive whole (its SHA-256 doesn't match)")
	}
	return f.Sync()
}

// unpack takes the release's files (and only those) out of its .tar.gz into dir.
func unpack(archive, dir string) error {
	f, err := os.Open(archive)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tr := tar.NewReader(gz)
	found := false
	for {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		name := filepath.Base(h.Name)
		if h.Typeflag != tar.TypeReg || !Files[name] || filepath.Clean(h.Name) != name {
			continue
		}
		out, err := os.OpenFile(filepath.Join(dir, name), os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
		if err != nil {
			return err
		}
		_, err = io.Copy(out, io.LimitReader(tr, 256<<20))
		if cerr := out.Close(); err == nil {
			err = cerr
		}
		if err != nil {
			return err
		}
		found = found || name == "touchstream-spoke"
	}
	if !found {
		return errors.New("the update has no spoke in it")
	}
	return nil
}

// Current is the version the box starts ("" for the one built into its system).
func Current(data string) string { return read(filepath.Join(Dir(data), "current")) }

// Failed is a version that was tried and didn't start, if there was one.
func Failed(data string) string { return read(filepath.Join(Dir(data), "failed")) }

// Confirm is called once the running spoke has reached its hub: if it's on trial, it's kept.
func Confirm(data, version string) (bool, error) {
	err := os.Remove(filepath.Join(Dir(data), version, ".trying"))
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	return err == nil, err
}

// Trying says whether this version is on trial (it hasn't reached its hub yet).
func Trying(data, version string) bool {
	_, err := os.Stat(filepath.Join(Dir(data), version, ".trying"))
	return err == nil
}

// prune removes versions other than the ones kept.
func prune(app string, keep ...string) {
	entries, _ := os.ReadDir(app)
	for _, e := range entries {
		if !e.IsDir() || !Release(e.Name()) {
			continue
		}
		kept := false
		for _, k := range keep {
			kept = kept || e.Name() == k
		}
		if !kept {
			_ = os.RemoveAll(filepath.Join(app, e.Name()))
		}
	}
}

func read(path string) string {
	b, _ := os.ReadFile(path)
	return strings.TrimSpace(string(b))
}

func write(path, value string) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(value+"\n"), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
