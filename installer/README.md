# The TouchStream installer

One app for Mac and Windows that sets up everything, with no terminal: it installs the hub (or finds it, or uses one
on another computer, or removes it), then asks where your spokes came from. One that came ready to go is found on the
network, paired and named; for a Luckfox PicoKVM of your own it writes a microSD card, paired with the hub. It also
writes plain cards (no hub, no name) for spokes that go to someone else. See [docs/spoke.md](../docs/spoke.md#install)
for using it.

It's [Wails](https://wails.io): Go for the work, a web page for the window (`frontend/dist`, plain JavaScript, no
build step).

| Package | What it does |
|---|---|
| `internal/hubsetup` | Finds the hub (ports 8080 to 8099), runs the hub's own install script (an embedded copy, kept the same as `hub/install` by a test) to install or remove it |
| `internal/hubapi` | Signs in to the hub and registers each spoke (`POST /api/spokes`), or forgets it if its card isn't written; adopts a ready-to-go spoke the hub sees on its network, and names it |
| `internal/cards` | Lists the cards to offer: removable SD or USB, 512 GB or less, never a system or APFS disk; on a Mac, opens one with an admin's password (macOS's `authopen`) |
| `internal/image` | Unpacks the image (`.xz`, `.gz`), writes it with the spoke's settings in its block, partition table last |
| `internal/flash` | Writes a card and reads it all back to check it |
| `internal/elevate` | Runs the writer with admin rights on Windows (its permission prompt) |

On Windows, the writer is the app itself, run as `TouchStream Installer write-card ...` with admin rights; it reports
progress through a file the app watches. On a Mac, the app writes the card itself: `authopen` asks for an admin's
password, opens the card and hands it back. (A program run as admin can't write a removable card there: macOS can't
ask anyone whether it may, so it's refused with "operation not permitted".)

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

A disk image of your own needs no password to write. On Windows, a VHD (`create vdisk` and `attach vdisk` in
diskpart) is a card in developer mode, written with the same writer, which needs an administrator. `wails dev` also serves the window at http://localhost:34115
to drive from a browser. The spoke's image needs its settings block (see the roadmap); for a fake one, any file
with `TOUCHSTREAM-PROVISION-V1` and a newline at a 64 KiB boundary does. To check a card, run the spoke emulator with
`-card card.dmg`: it pairs itself from the card's settings.

`TOUCHSTREAM_SOURCE` (developer mode) installs the hub from a local checkout instead of the release.

## Releasing

`tools/release-mac` builds the Mac app for Apple silicon and Intel, signs it with your Developer ID, puts it in a
disk image, and has Apple notarize it, so it opens with no warning. The top of the script says how to save your
notarization credentials once. The Windows app isn't signed yet, so Windows warns before it runs it.
