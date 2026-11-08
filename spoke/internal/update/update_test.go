package update

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// release makes a spoke release: a .tar.gz with a stand-in spoke that prints its version, plus extra files.
func release(t *testing.T, version string, extra map[string]string) []byte {
	t.Helper()
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	files := map[string]string{"touchstream-spoke": "#!/bin/sh\necho " + version + "\n"}
	for k, v := range extra {
		files[k] = v
	}
	for name, body := range files {
		if err := tw.WriteHeader(&tar.Header{Name: name, Mode: 0o755, Size: int64(len(body)), Typeflag: tar.TypeReg}); err != nil {
			t.Fatal(err)
		}
		tw.Write([]byte(body))
	}
	tw.Close()
	gz.Close()
	return buf.Bytes()
}

func signed(t *testing.T, key ed25519.PrivateKey, version string, body []byte) Offer {
	sum := sha256.Sum256(body)
	sha := hex.EncodeToString(sum[:])
	return Offer{Version: version, URL: "/api/spoke/update/" + version, SHA256: sha, Size: int64(len(body)),
		Signature: base64.StdEncoding.EncodeToString(ed25519.Sign(key, Message(version, sha)))}
}

func setup(t *testing.T, body []byte) (ed25519.PrivateKey, *httptest.Server, string) {
	t.Helper()
	public, private, _ := ed25519.GenerateKey(rand.Reader)
	old := PublicKey
	PublicKey = base64.StdEncoding.EncodeToString(public)
	t.Cleanup(func() { PublicKey = old })
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer tok" {
			http.Error(w, "no", http.StatusUnauthorized)
			return
		}
		w.Write(body)
	}))
	t.Cleanup(srv.Close)
	return private, srv, t.TempDir()
}

var auth = map[string]string{"Authorization": "Bearer tok"}

func TestInstallsASignedReleaseOnTrial(t *testing.T) {
	body := release(t, "0.3.0", map[string]string{"touchstream-capture": "helper", "evil.sh": "rm -rf /"})
	key, srv, data := setup(t, body)
	var last float64
	if err := Install(context.Background(), signed(t, key, "0.3.0", body), data, srv.URL, auth, func(p float64) { last = p }); err != nil {
		t.Fatal(err)
	}
	if last != 1 {
		t.Errorf("progress ended at %v", last)
	}
	if Current(data) != "0.3.0" || read(filepath.Join(Dir(data), "previous")) != "" {
		t.Errorf("current %q, previous %q", Current(data), read(filepath.Join(Dir(data), "previous")))
	}
	if !Trying(data, "0.3.0") {
		t.Error("a new version should start on trial")
	}
	if _, err := os.Stat(filepath.Join(Dir(data), "0.3.0", "touchstream-capture")); err != nil {
		t.Error("the capture helper should come with it")
	}
	if _, err := os.Stat(filepath.Join(Dir(data), "0.3.0", "evil.sh")); err == nil {
		t.Error("only the release's own files are unpacked")
	}
	if ok, err := Confirm(data, "0.3.0"); !ok || err != nil || Trying(data, "0.3.0") {
		t.Errorf("confirming: %v %v", ok, err)
	}

	// The next one keeps this one as the one before, and the one before that goes
	body2 := release(t, "0.4.0", nil)
	srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write(body2) })
	if err := Install(context.Background(), signed(t, key, "0.4.0", body2), data, srv.URL, auth, nil); err != nil {
		t.Fatal(err)
	}
	if Current(data) != "0.4.0" || read(filepath.Join(Dir(data), "previous")) != "0.3.0" {
		t.Errorf("current %q, previous %q", Current(data), read(filepath.Join(Dir(data), "previous")))
	}
}

func TestRefusesWhatIsntSignedOrWhole(t *testing.T) {
	body := release(t, "0.3.0", nil)
	key, srv, data := setup(t, body)
	_, other, _ := ed25519.GenerateKey(rand.Reader)

	forged := signed(t, other, "0.3.0", body)
	if err := Install(context.Background(), forged, data, srv.URL, auth, nil); err == nil || !strings.Contains(err.Error(), "signed") {
		t.Errorf("another key's signature: %v", err)
	}
	relabeled := signed(t, key, "0.3.0", body)
	relabeled.Version = "0.9.0" // the signature covers the version
	if err := Install(context.Background(), relabeled, data, srv.URL, auth, nil); err == nil {
		t.Error("a signature for another version was accepted")
	}
	damaged := signed(t, key, "0.3.0", append([]byte{}, body...))
	srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write(body[:len(body)-10]) })
	if err := Install(context.Background(), damaged, data, srv.URL, auth, nil); err == nil || !strings.Contains(err.Error(), "SHA-256") {
		t.Errorf("a cut-off download: %v", err)
	}
	if Current(data) != "" {
		t.Errorf("nothing should have been installed, current is %q", Current(data))
	}
}

func TestRefusesASpokeThatDoesntRunHere(t *testing.T) {
	body := release(t, "0.2.0", nil) // says it's 0.2.0, offered as 0.3.0
	key, srv, data := setup(t, body)
	if err := Install(context.Background(), signed(t, key, "0.3.0", body), data, srv.URL, auth, nil); err == nil {
		t.Error("a spoke that reports another version was installed")
	}
	if Current(data) != "" {
		t.Errorf("current is %q", Current(data))
	}
}

func TestReleaseVersions(t *testing.T) {
	for v, want := range map[string]bool{"0.3.0": true, "12.0.1": true, "0.1.0-dev": false, "dev": false, "v0.3.0": false, "": false} {
		if Release(v) != want {
			t.Errorf("Release(%q) = %v", v, !want)
		}
	}
}
