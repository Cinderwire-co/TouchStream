// TouchStream Lite: the spoke for small off-the-shelf HDMI capture boxes (first, the Luckfox
// PicoKVM). It does what the Pi spoke does without its touchscreen: streams to the hub over
// SRT, records its own copy and uploads it, and can be found and adopted by a hub on the LAN.
//
//	touchstream-lite run [-data DIR] [-source replay:clip.ts]   run the spoke
//	touchstream-lite pair HUB CODE                               pair with a hub (code from Add spoke)
//	touchstream-lite unpair                                      forget the hub
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	_ "time/tzdata" // the box has no zone files: set TZ, e.g. America/Chicago

	"github.com/Cinderwire-co/TouchStream/lite/internal/config"
	"github.com/Cinderwire-co/TouchStream/lite/internal/discovery"
	"github.com/Cinderwire-co/TouchStream/lite/internal/hub"
	"github.com/Cinderwire-co/TouchStream/lite/internal/media"
	"github.com/Cinderwire-co/TouchStream/lite/internal/recorder"
	"github.com/Cinderwire-co/TouchStream/lite/internal/screen"
	"github.com/Cinderwire-co/TouchStream/lite/internal/share"
	"github.com/Cinderwire-co/TouchStream/lite/internal/spoke"
	"github.com/Cinderwire-co/TouchStream/lite/internal/uplink"
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
	address := flags.String("address", "", "the address phones reach this box at (default: its own)")
	_ = flags.Parse(args)

	store, err := config.Open(*data)
	if err != nil {
		log.Fatal(err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	switch command {
	case "run":
		err = run(ctx, store, *data, *source, *address)
	case "pair":
		if flags.NArg() != 2 {
			log.Fatal("usage: touchstream-lite pair HUB CODE")
		}
		if err = hub.Pair(ctx, store, flags.Arg(0), flags.Arg(1), spoke.Version); err == nil {
			fmt.Println("Connected to", store.Get().HubURL)
		}
	case "unpair":
		err = hub.Unpair(store)
	default:
		err = fmt.Errorf("unknown command %q (run, pair or unpair)", command)
	}
	if err != nil && !errors.Is(err, context.Canceled) {
		log.Fatal(err)
	}
}

func run(ctx context.Context, store *config.Store, data, source, address string) error {
	var src media.Source
	var replay *media.Replay // the emulator's stand-in for the HDMI input
	switch {
	case strings.HasPrefix(source, "replay:"):
		replay = &media.Replay{Path: strings.TrimPrefix(source, "replay:"),
			Format: media.Signal{Present: true, Width: 1920, Height: 1080, FPS: 30}}
		src = replay
	case source == "hdmi":
		return errors.New("the PicoKVM's HDMI capture isn't built yet: use -source replay:FILE.ts")
	default:
		return fmt.Errorf("unknown source %q", source)
	}

	rec := &recorder.Recorder{Dir: filepath.Join(data, "recordings")}
	s := &spoke.Spoke{Store: store, Source: src, Uplink: uplink.New(), Recorder: rec,
		Uploader: &recorder.Uploader{Recorder: rec, Store: store}}
	s.Link = &hub.Link{Store: store, Status: s.Status, OnDesired: s.Apply}
	log.Printf("TouchStream Lite %s, %s", spoke.Version, store.Get().DeviceName)

	var out media.Fanout
	toUplink, toRecorder := out.Subscribe(256), out.Subscribe(1024)
	go s.Uplink.Run(ctx, toUplink)
	go rec.Run(toRecorder)
	go s.Uploader.Run(ctx)
	go s.Link.Run(ctx)
	go discovery.Beacon(ctx, store)

	mux := http.NewServeMux()
	discovery.Routes(mux, store, spoke.Version)
	// Recordings to phones: the screen's QR codes open pages served from here
	addr := func() string { return nonEmpty(address, discovery.LocalIP()) }
	shares := &share.Shares{Recorder: rec, Dir: filepath.Join(data, "cache", "downloads"),
		Name: func() string { return store.Get().DeviceName }}
	shares.Routes(mux)
	// The box's touchscreen. In the emulator it's a page to click: /screen
	panel := &screen.Screen{
		State: func() spoke.State { return s.State(addr()) }, Record: s.ToggleRecord,
		Sessions: rec.Sessions, Prepared: shares.State,
		Share: func(name string) string {
			return fmt.Sprintf("http://%s:%d/r/%s", addr(), discovery.HTTPPort, shares.Link(name))
		},
	}
	screen.Routes(mux, panel)
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

func nonEmpty(v, fallback string) string {
	if v == "" {
		return fallback
	}
	return v
}
