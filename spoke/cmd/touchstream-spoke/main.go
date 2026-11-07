// The TouchStream spoke, for the Luckfox PicoKVM: an off-the-shelf HDMI capture box. It streams
// to the hub over SRT, records its own copy and uploads it, shows what it's doing on the box's
// screen, puts recordings on phones, and can be found and adopted by a hub on the LAN.
//
//	touchstream-spoke run [-data DIR] [-source replay:clip.ts]   run the spoke
//	                      [-card FILE]                           read the installer's settings from FILE
//	touchstream-spoke pair HUB CODE                               pair with a hub (code from Add spoke)
//	touchstream-spoke unpair                                      forget the hub
//	touchstream-spoke splash [-fb DEV]                            the splash on the box's screen (at startup)
//	touchstream-spoke install [-keep-card] [-bootloader]          from the installer's card onto the box itself
//	touchstream-spoke card                                        make the installer's card, once it's done, one for recordings
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package main

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	_ "time/tzdata" // the box has no zone files: set TZ, e.g. America/Chicago

	"github.com/Cinderwire-co/TouchStream/spoke/internal/config"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/direct"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/discovery"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/hub"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/install"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/media"
	lcd "github.com/Cinderwire-co/TouchStream/spoke/internal/panel"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/provision"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/recorder"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/screen"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/setup"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/share"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/spoke"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/uplink"
)

func main() {
	log.SetFlags(0)
	command, args := "run", os.Args[1:]
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		command, args = args[0], args[1:]
	}
	flags := flag.NewFlagSet(command, flag.ExitOnError)
	data := flags.String("data", "/userdata/touchstream", "folder for settings and recordings")
	source := flags.String("source", "hdmi", "hdmi, or replay:FILE.ts to replay a clip (the emulator)")
	address := flags.String("address", "", "the address (and :port) phones reach this box at (default: its own)")
	card := flags.String("card", "", "where the installer's settings are (default: the box's card)")
	capture := flags.String("capture", "touchstream-capture", "the HDMI capture helper, and its flags")
	audio := flags.String("audio", "-f alsa -ar 48000 -ac 2 -i hw:0", "ffmpeg's input for the HDMI sound (the box's only sound card)")
	fb := flags.String("fb", "/dev/fb0", "the box's screen (a framebuffer), if it has one")
	touch := flags.String("touch", "/dev/input/event0", "the box's touch (evdev), if it has one")
	keepCard := flags.Bool("keep-card", false, "install: the card can still start the box (to try its own storage with the card out)")
	bootloader := flags.Bool("bootloader", false, "install: the card's boot loader goes onto the box too")
	_ = flags.Parse(args)
	if command == "splash" { // (early in the box's startup: before its data partition's there)
		if err := lcd.Show(*fb, screen.Splash("Starting…"), true); err != nil {
			log.Fatal(err)
		}
		return
	}

	store, err := config.Open(*data)
	if err != nil {
		log.Fatal(err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	switch command {
	case "run":
		devices := provision.Devices
		if *card != "" {
			devices = []string{*card}
		}
		if did, perr := provision.Apply(store, devices); perr != nil {
			log.Printf("the installer's settings on the card: %v", perr)
		} else if did != "" {
			log.Print(did)
		}
		err = run(ctx, store, *data, *source, *address, strings.Fields(*capture), strings.Fields(*audio), *fb, *touch)
	case "pair":
		if flags.NArg() != 2 {
			log.Fatal("usage: touchstream-spoke pair HUB CODE")
		}
		if err = hub.Pair(ctx, store, flags.Arg(0), flags.Arg(1), spoke.Version); err == nil {
			fmt.Println("Connected to", store.Get().HubURL)
		}
	case "unpair":
		err = hub.Unpair(store)
	case "install":
		// Started from the installer's card: the spoke goes onto the box itself, paired as the card says
		if did, perr := provision.Apply(store, provision.Devices); perr != nil {
			log.Printf("the installer's settings on the card: %v", perr)
		} else if did != "" {
			log.Print(did)
		}
		say := func(line string) {
			if !strings.HasPrefix(line, "Installing") {
				log.Print(line)
			}
			_ = lcd.Show(*fb, screen.Splash(line), true)
		}
		say("Installing… 0%")
		err = install.ToEMMC(install.Card, install.EMMC, *data, install.Options{Bootloader: *bootloader, KeepCard: *keepCard}, say)
		if err == nil {
			say("Starting again…")
		} else {
			say("Not installed: see the log")
		}
	case "card":
		var did bool
		if did, err = install.Recordings(install.Card); did {
			log.Print("The installer's card is ready for recordings")
		}
	default:
		err = fmt.Errorf("unknown command %q (run, pair, unpair, splash, install or card)", command)
	}
	if err != nil && !errors.Is(err, context.Canceled) {
		log.Fatal(err)
	}
}

func run(ctx context.Context, store *config.Store, data, source, address string, capture, audio []string, fb, touch string) error {
	var src media.Source
	var replay *media.Replay // the emulator's stand-in for the HDMI input
	switch {
	case strings.HasPrefix(source, "replay:"):
		replay = &media.Replay{Path: strings.TrimPrefix(source, "replay:"),
			Format: media.Signal{Present: true, Width: 1920, Height: 1080, FPS: 30}}
		src = replay
	case source == "hdmi":
		if _, err := exec.LookPath(capture[0]); err != nil {
			// No capture helper (an image made before it): the spoke still starts, pairs and shows on its screen
			log.Printf("No HDMI capture helper (%s): no signal", capture[0])
			src = media.NoSignal{}
			break
		}
		src = &media.Capture{Helper: capture, Audio: audio, Settings: func() (string, int) {
			c := store.Get()
			return nonEmpty(c.Framerate, "30"), cmp.Or(c.BitrateKbps, 8000)
		}}
	default:
		return fmt.Errorf("unknown source %q", source)
	}

	rec := &recorder.Recorder{Dir: filepath.Join(data, "recordings")}
	s := &spoke.Spoke{Store: store, Source: src, Uplink: uplink.New(), Recorder: rec,
		Uploader: &recorder.Uploader{Recorder: rec, Store: store}, Direct: direct.New(), Data: data}
	s.Link = &hub.Link{Store: store, Status: s.Status, OnDesired: s.Apply}
	log.Printf("TouchStream spoke %s, %s", spoke.Version, store.Get().DeviceName)

	var out media.Fanout
	toUplink, toRecorder, toDirect := out.Subscribe(256), out.Subscribe(1024), out.Subscribe(256)
	go s.Uplink.Run(ctx, toUplink)
	go rec.Run(toRecorder)
	go s.Direct.Run(ctx, toDirect)
	go s.Watch(ctx)
	go s.Uploader.Run(ctx)
	go s.Link.Run(ctx)
	go keepGoing(ctx, "Beacon", func(ctx context.Context) error { return discovery.Beacon(ctx, store) })
	go s.FindZone(ctx)

	mux := http.NewServeMux()
	discovery.Routes(mux, store, spoke.Version)
	// Recordings to phones: the screen's QR codes open pages served from here
	addr := func() string { // host:port for links
		if strings.Contains(address, ":") {
			return address
		}
		return fmt.Sprintf("%s:%d", nonEmpty(address, discovery.LocalIP()), discovery.HTTPPort)
	}
	shares := &share.Shares{Recorder: rec, Dir: filepath.Join(data, "cache", "downloads"),
		Name: func() string { return store.Get().DeviceName }}
	shares.Routes(mux)
	settings := &setup.Setup{Store: store, Version: spoke.Version, Rates: spoke.Rates}
	settings.Routes(mux)
	// The box's touchscreen. In the emulator it's a page to click: /screen
	panel := &screen.Screen{
		State: func() spoke.State { return s.State(strings.Split(addr(), ":")[0]) }, Record: s.ToggleRecord, Live: s.GoLive,
		Sessions: rec.Sessions, Prepared: shares.State,
		Setup: func() string {
			return fmt.Sprintf("http://%s/setup/%s", addr(), settings.Link())
		},
		Share: func(name string) string {
			return fmt.Sprintf("http://%s/r/%s", addr(), shares.Link(name))
		},
		Restart: func() { restartBox("the BOOT button") },
		Reset:   func() { backToSetup(store) },
	}
	settings.Saved = panel.Saved
	screen.Routes(mux, panel)
	// The box's own screen and touch, turned round as they are in the case (the emulator has
	// neither: its screen is the /screen page)
	display := &lcd.Display{Path: fb, Rotate180: true, Render: panel.Render}
	if _, err := os.Stat(fb); err == nil {
		go keepGoing(ctx, "The screen", display.Run)
	}
	if _, err := os.Stat(touch); err == nil {
		woke := func(on func(x, y int)) func(x, y int) { return func(x, y int) { on(x, y); display.Wake() } }
		t := &lcd.Touch{Path: touch, Rotate180: true, Size: screen.Size, Down: woke(panel.Down), Move: woke(panel.Move), Up: woke(panel.Up)}
		go keepGoing(ctx, "Touch", t.Run)
	}
	if button := lcd.Input(lcd.BootButton); button != "" {
		k := &lcd.Key{Path: button, Code: lcd.KeyBoot, Press: func(down bool) { panel.Button(down); display.Wake() }}
		go keepGoing(ctx, "The BOOT button", k.Run)
	}
	// Started well: the A/B slot it started from is marked so, or U-Boot would start the other in time
	go func() {
		select {
		case <-ctx.Done():
		case <-time.After(30 * time.Second):
			cmdline, _ := os.ReadFile("/proc/cmdline")
			if did, err := install.MarkGood(install.Misc, string(cmdline)); err != nil && !errors.Is(err, os.ErrNotExist) {
				log.Printf("Marking this start good: %v", err)
			} else if did {
				log.Print("This start's marked good")
			}
		}
	}()
	if replay != nil {
		emulatorRoutes(mux, replay, store, panel)
	}
	server := &http.Server{Addr: fmt.Sprintf(":%d", discovery.HTTPPort), Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	go func() {
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf("HTTP server: %v", err)
		}
	}()
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()

	err := src.Run(ctx, out.Emit)
	s.Recorder.Stop() // close the last part cleanly on the way out
	return err
}

// emulatorRoutes let the emulator's page pull the HDMI cable out and plug it back in.
func emulatorRoutes(mux *http.ServeMux, replay *media.Replay, store *config.Store, panel *screen.Screen) {
	mux.HandleFunc("GET /emulator", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"hdmi": replay.Signal().Present, "hub": store.Get().HubURL,
			"link": panel.Link()})
	})
	mux.HandleFunc("POST /emulator/hdmi", func(w http.ResponseWriter, r *http.Request) {
		in := r.URL.Query().Get("in") == "1"
		replay.Plug(in)
		log.Printf("Emulator: HDMI cable %s", map[bool]string{true: "plugged in", false: "pulled out"}[in])
		w.WriteHeader(http.StatusNoContent)
	})
}

// keepGoing runs fn, and again 2 s after it stops (a device's driver reset, say), until ctx ends;
// it says why only when the reason changes.
// restartBox starts the box again.
func restartBox(why string) {
	log.Printf("Restarting (%s)", why)
	if out, err := exec.Command("reboot").CombinedOutput(); err != nil {
		log.Printf("Restarting: %v %s", err, out)
	}
}

// backToSetup forgets the hub and the spoke's settings (its recordings stay), and starts it again:
// it's as it came, with its own name, to pair or set up anew.
func backToSetup(store *config.Store) {
	err := store.Update(func(c *config.Config) {
		*c = config.Config{DeviceName: config.DefaultName(), FoundZone: c.FoundZone,
			Provisioned: c.Provisioned} // (a card's settings, once taken, aren't taken again)
	})
	if err != nil {
		log.Printf("Back to setup: %v", err)
		return
	}
	restartBox("back to setup")
}

func keepGoing(ctx context.Context, what string, fn func(context.Context) error) {
	var said string
	for ctx.Err() == nil {
		if err := fn(ctx); err != nil && err.Error() != said {
			said = err.Error()
			log.Printf("%s: %v (trying again)", what, err)
		}
		select {
		case <-ctx.Done():
		case <-time.After(2 * time.Second):
		}
	}
}

func nonEmpty(v, fallback string) string {
	if v == "" {
		return fallback
	}
	return v
}
