// The TouchStream installer, for Mac and Windows: it installs the hub on this computer (or finds
// one), then writes a microSD card for each spoke, already paired with the hub.
//
//	TouchStream Installer                     the app
//	TouchStream Installer write-card ...      writes one card (run by the app, as admin)
//
// TOUCHSTREAM_INSTALLER_DEV=1 is developer mode: disk images count as cards, an image file can be
// picked, and TOUCHSTREAM_SOURCE installs the hub from a folder.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package main

import (
	"embed"
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
	"github.com/wailsapp/wails/v2/pkg/options/mac"
	"github.com/wailsapp/wails/v2/pkg/options/windows"

	"github.com/Cinderwire-co/TouchStream/installer/internal/cards"
	"github.com/Cinderwire-co/TouchStream/installer/internal/elevate"
	"github.com/Cinderwire-co/TouchStream/installer/internal/flash"
	"github.com/Cinderwire-co/TouchStream/installer/internal/image"
)

const Version = "0.1.0-dev"

//go:embed all:frontend/dist
var assets embed.FS

func main() {
	if len(os.Args) > 1 && os.Args[1] == "write-card" {
		os.Exit(writeCard(os.Args[2:]))
	}
	app := NewApp(os.Getenv("TOUCHSTREAM_INSTALLER_DEV") == "1")
	err := wails.Run(&options.App{
		Title:            "TouchStream Installer",
		Width:            860,
		Height:           640,
		MinWidth:         760,
		MinHeight:        580,
		AssetServer:      &assetserver.Options{Assets: assets},
		BackgroundColour: &options.RGBA{R: 14, G: 14, B: 14, A: 255},
		OnStartup:        app.startup,
		Bind:             []any{app},
		Mac: &mac.Options{
			TitleBar:   mac.TitleBarHiddenInset(),
			Appearance: mac.NSAppearanceNameDarkAqua,
			About:      &mac.AboutInfo{Title: "TouchStream Installer", Message: "Made by Cinderwire · MIT License"},
		},
		Windows: &windows.Options{Theme: windows.Dark},
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

// writeCard is the copy of the installer run as admin: it writes one card and reports through
// the progress file, then exits.
func writeCard(args []string) int {
	flags := flag.NewFlagSet("write-card", flag.ContinueOnError)
	cardFile := flags.String("card", "", "the card, as JSON")
	imagePath := flags.String("image", "", "the spoke's image")
	provisionFile := flags.String("provision", "", "the spoke's settings, as JSON (optional)")
	progress := flags.String("progress", "", "where to report progress")
	if flags.Parse(args) != nil || *cardFile == "" || *imagePath == "" || *progress == "" {
		return 2
	}
	fail := func(err error) int {
		elevate.Report(*progress, elevate.Progress{Phase: "failed", Error: err.Error()})
		return 1
	}
	keepAwake()
	var card cards.Card
	if err := readJSON(*cardFile, &card); err != nil {
		return fail(err)
	}
	var p *image.Provision
	if *provisionFile != "" {
		p = &image.Provision{}
		if err := readJSON(*provisionFile, p); err != nil {
			return fail(err)
		}
	}
	target, err := cards.Open(card)
	if err != nil {
		return fail(err)
	}
	defer target.Close()
	if err := flash.Card(target, *imagePath, p, func(pr elevate.Progress) { elevate.Report(*progress, pr) }); err != nil {
		return fail(err)
	}
	return 0
}

func readJSON(path string, v any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, v)
}
