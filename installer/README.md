# The TouchStream installer

One app for Mac and Windows that sets up everything, with no terminal: it installs the hub (or finds it, or uses one
on another computer), asks whether you're adding spokes, checks you have a Luckfox PicoKVM with its screen, then
writes a microSD card for each spoke, paired with the hub. See [docs/spoke.md](../docs/spoke.md#install) for using it.

It's [Wails](https://wails.io): Go for the work, a web page for the window (`frontend/dist`, plain JavaScript, no
build step).

| Package | What it does |
|---|---|
| `internal/hubsetup` | Finds the hub (ports 8080 to 8099), runs the hub's own install script (an embedded copy, kept the same as `hub/install` by a test) |
| `internal/hubapi` | Signs in to the hub and registers each spoke (`POST /api/spokes`), or forgets it if its card isn't written |
| `internal/cards` | Lists the cards to offer: removable SD or USB, 512 GB or less, never a system or APFS disk |
| `internal/image` | Unpacks the image (`.xz`, `.gz`), writes it with the spoke's settings in its block, partition table last |
| `internal/flash` | Writes a card and reads it all back to check it |
| `internal/elevate` | Runs the writer with admin rights: the Mac's password prompt, or Windows' permission prompt |

The writer is the app itself, run as `TouchStream Installer write-card ...` with admin rights; it reports progress
through a file the app watches.

## Building

You need Go and the Wails CLI (`go install github.com/wailsapp/wails/v2/cmd/wails@v2.17.1`).

```bash
wails build -platform darwin/universal -skipbindings   # build/bin/TouchStream Installer.app
wails build -platform windows/amd64 -skipbindings      # build/bin/TouchStream Installer.exe
go test ./...
```

## Trying it without a card

Developer mode offers disk images you've attached as cards, and takes the spoke's image from a file, as no image is
released yet:

```bash
hdiutil create -size 64m -fs "MS-DOS FAT32" -volname OLDCARD -layout MBRSPUD card.dmg && hdiutil attach card.dmg
TOUCHSTREAM_INSTALLER_DEV=1 TOUCHSTREAM_SPOKE_IMAGE=spoke.img.xz wails dev -s -skipbindings
```

A disk image of your own needs no password to write. `wails dev` also serves the window at http://localhost:34115
to drive from a browser. The spoke's image needs its settings block (see the roadmap); for a fake one, any file
with `TOUCHSTREAM-PROVISION-V1` and a newline at a 64 KiB boundary does. To check a card, run the spoke emulator with
`-card card.dmg`: it pairs itself from the card's settings.

`TOUCHSTREAM_SOURCE` (developer mode) installs the hub from a local checkout instead of the release.

## Releasing

`tools/release-mac` builds the Mac app for Apple silicon and Intel, signs it with your Developer ID, puts it in a
disk image, and has Apple notarize it, so it opens with no warning. The top of the script says how to save your
notarization credentials once. The Windows app isn't signed yet, so Windows warns before it runs it.
