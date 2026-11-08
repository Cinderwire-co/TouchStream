package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"

	wails "github.com/wailsapp/wails/v2/pkg/runtime"

	"github.com/Cinderwire-co/TouchStream/installer/internal/cards"
	"github.com/Cinderwire-co/TouchStream/installer/internal/elevate"
	"github.com/Cinderwire-co/TouchStream/installer/internal/flash"
	"github.com/Cinderwire-co/TouchStream/installer/internal/hubapi"
	"github.com/Cinderwire-co/TouchStream/installer/internal/hubsetup"
	"github.com/Cinderwire-co/TouchStream/installer/internal/image"
)

// App is what the installer's pages call. Long jobs report through events: hub:line while the hub
// installs, image:progress while the spoke's software downloads, card:progress while a card is written.
type App struct {
	ctx   context.Context
	dev   bool
	cache string

	mu       sync.Mutex
	hub      *hubapi.Client // signed in, once it is
	image    string         // the spoke's image, ready to write
	settings bool           // it has a settings block, so cards can be paired with the hub
}

func NewApp(dev bool) *App {
	cache, _ := os.UserCacheDir()
	return &App{dev: dev, cache: filepath.Join(cache, "TouchStream Installer")}
}

func (a *App) startup(ctx context.Context) {
	a.ctx = ctx
	_ = os.MkdirAll(a.cache, 0o755)
}

// Info is about this computer and the installer.
func (a *App) Info() map[string]any {
	return map[string]any{"os": runtime.GOOS, "version": Version, "dev": a.dev}
}

func (a *App) OpenURL(url string) { wails.BrowserOpenURL(a.ctx, url) }

// ---- The hub ----

func (a *App) FindHub() hubsetup.Found { return hubsetup.Find(a.ctx) }

func (a *App) NeedsHomebrew() bool { return hubsetup.NeedsHomebrew() }

func (a *App) HomebrewPackage() { wails.BrowserOpenURL(a.ctx, hubsetup.HomebrewPackage) }

// release matches a released version (0.2.0), not a developer's build (0.1.0-dev).
var release = regexp.MustCompile(`^\d+\.\d+\.\d+$`)

// InstallHub installs or updates the hub here, and returns the dashboard's address.
func (a *App) InstallHub() (string, error) {
	source := ""
	if a.dev {
		source = os.Getenv("TOUCHSTREAM_SOURCE")
	} else if release.MatchString(Version) { // a released installer installs the hub of its own release
		source = fmt.Sprintf("https://github.com/Cinderwire-co/TouchStream/archive/refs/tags/v%s.zip#subdirectory=hub", Version)
	}
	return hubsetup.Install(a.ctx, source, func(l hubsetup.Line) { wails.EventsEmit(a.ctx, "hub:line", l) })
}

// UseHub checks there's a hub at address (one on another computer, say).
func (a *App) UseHub(address string) (hubsetup.Found, error) {
	address = strings.TrimSpace(address)
	if !strings.Contains(address, "://") {
		address = "http://" + address
	}
	ctx, cancel := context.WithTimeout(a.ctx, 5*time.Second)
	defer cancel()
	found, err := hubsetup.Probe(ctx, address)
	if err != nil {
		return found, fmt.Errorf("no TouchStream hub answers at %s", address)
	}
	return found, nil
}

// SetPassword chooses a new hub's dashboard password (and signs in with it).
func (a *App) SetPassword(url, password string) error {
	if len(password) < 8 {
		return errors.New("use at least 8 characters")
	}
	return a.signIn(url, func(c *hubapi.Client) error { return c.Setup(a.ctx, password) })
}

// SignIn signs in to a hub that's set up already.
func (a *App) SignIn(url, password string) error {
	return a.signIn(url, func(c *hubapi.Client) error { return c.SignIn(a.ctx, password) })
}

func (a *App) signIn(url string, how func(*hubapi.Client) error) error {
	c := hubapi.New(url)
	if err := how(c); err != nil {
		return err
	}
	a.mu.Lock()
	a.hub = c
	a.mu.Unlock()
	return nil
}

// ---- The spoke's software ----

// ImageState is the spoke's image: ready to write, or why not.
type ImageState struct {
	Ready    bool   `json:"ready"`
	Size     int64  `json:"size"`
	Settings bool   `json:"settings"` // cards can be paired with the hub (false: adopt them in the dashboard)
	Version  string `json:"version,omitempty"`
	Source   string `json:"source,omitempty"`
}

const releases = "https://api.github.com/repos/Cinderwire-co/TouchStream/releases?per_page=20"

// GetImage downloads the latest spoke image (checking it against its SHA-256), once.
func (a *App) GetImage() (ImageState, error) {
	if a.dev && os.Getenv("TOUCHSTREAM_SPOKE_IMAGE") != "" {
		return a.useImage(os.Getenv("TOUCHSTREAM_SPOKE_IMAGE"), "")
	}
	asset, sum, version, err := latestImage(a.ctx)
	if err != nil {
		return ImageState{}, err
	}
	name := filepath.Base(asset)
	path := filepath.Join(a.cache, name)
	if have, err := fileSum(path); err != nil || have != sum {
		if err := a.download(asset, path, sum); err != nil {
			return ImageState{}, err
		}
	}
	return a.useImage(path, version)
}

// PickImage writes a spoke image file from this computer instead (developer mode).
func (a *App) PickImage() (ImageState, error) {
	path, err := wails.OpenFileDialog(a.ctx, wails.OpenDialogOptions{Title: "The spoke's image",
		Filters: []wails.FileFilter{{DisplayName: "Spoke images (*.img, *.img.xz, *.img.gz)", Pattern: "*.img;*.img.xz;*.img.gz;*.xz;*.gz"}}})
	if err != nil || path == "" {
		return ImageState{}, err
	}
	return a.useImage(path, "")
}

func (a *App) useImage(path, version string) (ImageState, error) {
	wails.EventsEmit(a.ctx, "image:progress", map[string]any{"phase": "unpacking"})
	plain := path
	if strings.HasSuffix(path, ".xz") || strings.HasSuffix(path, ".gz") {
		plain = filepath.Join(a.cache, strings.TrimSuffix(strings.TrimSuffix(filepath.Base(path), ".xz"), ".gz"))
	}
	size, err := image.Unpack(path, plain)
	if err != nil {
		return ImageState{}, err
	}
	f, err := os.Open(plain)
	if err != nil {
		return ImageState{}, err
	}
	settings, err := image.HasBlock(f)
	f.Close()
	if err != nil {
		return ImageState{}, err
	}
	a.mu.Lock()
	a.image, a.settings = plain, settings
	a.mu.Unlock()
	return ImageState{Ready: true, Size: size, Settings: settings, Version: version, Source: path}, nil
}

func latestImage(ctx context.Context) (asset, sum, version string, err error) {
	req, _ := http.NewRequestWithContext(ctx, "GET", releases, nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", "", "", errors.New("can't reach GitHub to get the spoke's software: is this computer online?")
	}
	defer resp.Body.Close()
	var list []struct {
		Tag    string `json:"tag_name"`
		Draft  bool   `json:"draft"`
		Assets []struct {
			Name string `json:"name"`
			URL  string `json:"browser_download_url"`
		} `json:"assets"`
	}
	if resp.StatusCode != 200 || json.NewDecoder(resp.Body).Decode(&list) != nil {
		return "", "", "", errors.New("the spoke's software hasn't been released yet")
	}
	for _, r := range list {
		urls := map[string]string{}
		for _, as := range r.Assets {
			urls[as.Name] = as.URL
		}
		for name, url := range urls {
			if r.Draft || !strings.HasPrefix(name, "touchstream-spoke-picokvm-") || !strings.HasSuffix(name, ".img.xz") {
				continue
			}
			sumURL, ok := urls[name+".sha256"]
			if !ok {
				continue
			}
			s, err := getText(ctx, sumURL)
			if err != nil {
				return "", "", "", err
			}
			return url, strings.Fields(s + " x")[0], r.Tag, nil
		}
	}
	return "", "", "", errors.New("the spoke's software hasn't been released yet")
}

func (a *App) download(url, path, sum string) error {
	req, _ := http.NewRequestWithContext(a.ctx, "GET", url, nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil || resp.StatusCode != 200 {
		return errors.New("the download didn't work: check the internet connection and try again")
	}
	defer resp.Body.Close()
	out, err := os.Create(path + ".part")
	if err != nil {
		return err
	}
	hash := sha256.New()
	var done int64
	buf := make([]byte, 1<<20)
	last := time.Time{}
	for {
		n, rerr := resp.Body.Read(buf)
		if n > 0 {
			out.Write(buf[:n])
			hash.Write(buf[:n])
			done += int64(n)
			if time.Since(last) > 200*time.Millisecond {
				last = time.Now()
				wails.EventsEmit(a.ctx, "image:progress", map[string]any{"phase": "downloading", "done": done, "total": resp.ContentLength})
			}
		}
		if rerr == io.EOF {
			break
		}
		if rerr != nil {
			out.Close()
			return errors.New("the download stopped part way: try again")
		}
	}
	out.Close()
	if hex.EncodeToString(hash.Sum(nil)) != sum {
		os.Remove(path + ".part")
		return errors.New("the download came out wrong (it didn't match its checksum): try again")
	}
	return os.Rename(path+".part", path)
}

func getText(ctx context.Context, url string) (string, error) {
	req, _ := http.NewRequestWithContext(ctx, "GET", url, nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 4096))
	return string(data), err
}

func fileSum(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// ---- Cards ----

func (a *App) Cards() ([]cards.Card, error) { return cards.List(a.dev) }

// Written is how writing a card went.
type Written struct {
	Paired bool   `json:"paired"` // with the hub, from its first boot
	Note   string `json:"note,omitempty"`
}

// WriteCard writes the spoke called name to card: it registers the spoke with the hub (when signed
// in), then writes and checks the card (asking for admin rights), then ejects it.
func (a *App) WriteCard(card cards.Card, name string) (Written, error) {
	a.mu.Lock()
	img, hub, settings := a.image, a.hub, a.settings
	a.mu.Unlock()
	if img == "" {
		return Written{}, errors.New("the spoke's software isn't ready yet")
	}
	now, err := cards.List(a.dev)
	if err != nil {
		return Written{}, err
	}
	if err := cards.Same(card, now); err != nil {
		return Written{}, err
	}
	info, err := os.Stat(img)
	if err != nil {
		return Written{}, err
	}
	if card.Size < image.Padded(info.Size()) {
		return Written{}, flash.ErrTooSmall
	}
	p := &image.Provision{Name: strings.TrimSpace(name)}
	if !settings {
		p = nil // nowhere on the card for them: it's adopted in the dashboard instead
	}
	var spoke hubapi.Spoke
	if hub != nil && p != nil {
		if spoke, err = hub.MakeSpoke(a.ctx, p.Name); err != nil {
			return Written{}, fmt.Errorf("registering %s with the hub: %w", p.Name, err)
		}
		p.HubURL, p.SpokeID, p.Token = spoke.HubURL, spoke.ID, spoke.Token
	}
	err = a.write(card, img, p)
	if err != nil {
		if spoke.ID != "" {
			_ = hub.ForgetSpoke(a.ctx, spoke.ID) // its card didn't get written
		}
		return Written{}, err
	}
	_ = cards.Eject(card)
	w := Written{Paired: spoke.ID != ""}
	var notes []string
	if p == nil {
		notes = append(notes, "This version of the spoke's software can't take the hub's address, so the card isn't paired.")
	}
	if runtime.GOOS == "windows" {
		notes = append(notes, "If Windows offers to format the card, choose Cancel: it's written, Windows just can't read it.")
	}
	w.Note = strings.Join(notes, " ")
	return w, nil
}

func (a *App) write(card cards.Card, img string, p *image.Provision) error {
	dir, err := os.MkdirTemp("", "touchstream-card-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(dir)
	files := map[string]any{"card.json": card}
	if p != nil {
		files["settings.json"] = p
	}
	for name, v := range files {
		data, _ := json.Marshal(v)
		if err := os.WriteFile(filepath.Join(dir, name), data, 0o600); err != nil {
			return err
		}
	}
	progress := filepath.Join(dir, "progress.json")
	if err := cards.Unmount(card); err != nil {
		return err
	}
	stop := make(chan struct{})
	go elevate.Watch(progress, 150*time.Millisecond, stop, func(p elevate.Progress) { wails.EventsEmit(a.ctx, "card:progress", p) })
	defer close(stop)
	args := []string{"write-card", "--card", filepath.Join(dir, "card.json"), "--image", img, "--progress", progress}
	if p != nil {
		args = append(args, "--provision", filepath.Join(dir, "settings.json"))
	}
	// A Mac's app writes the card itself (it asks for an admin's password as it opens it), as does any
	// app for a disk image of a developer's own; Windows runs the writer as admin.
	if runtime.GOOS == "darwin" || cards.Writable(card) {
		if code := writeCard(args[1:]); code != 0 {
			return lastError(progress)
		}
		return nil
	}
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	if err := elevate.Run(exe, args...); err != nil {
		if errors.Is(err, elevate.ErrCanceled) {
			return err
		}
		return lastError(progress)
	}
	return nil
}

// lastError is why the writer said it failed.
func lastError(progress string) error {
	var p elevate.Progress
	if data, err := os.ReadFile(progress); err == nil && json.Unmarshal(data, &p) == nil && p.Error != "" {
		return errors.New(p.Error)
	}
	return errors.New("the card didn't get written: try again, or try another card")
}
