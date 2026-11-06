package cards

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
)

// What PowerShell says about each disk; Removable is Windows' own "Removable Media", which a card
// reader is and a USB backup drive isn't.
const listDisks = `
$media = @{}; Get-CimInstance Win32_DiskDrive | ForEach-Object { $media[[int]$_.Index] = [string]$_.MediaType }
@(Get-Disk | ForEach-Object {
  $d = $_
  [pscustomobject]@{
    Number = $d.Number; Name = [string]$d.FriendlyName; Size = [int64]$d.Size; Bus = $d.BusType.ToString()
    System = [bool]($d.IsSystem -or $d.IsBoot); ReadOnly = [bool]$d.IsReadOnly
    Removable = [bool]($media[[int]$d.Number] -like 'Removable*')
    Volumes = @(Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue | Get-Volume -ErrorAction SilentlyContinue |
      ForEach-Object { if ($_.FileSystemLabel) { $_.FileSystemLabel } elseif ($_.DriveLetter) { "$($_.DriveLetter):" } })
  }
}) | ConvertTo-Json -Compress -Depth 3`

func powershell(script string) ([]byte, error) {
	cmd := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	return cmd.Output()
}

// List finds the cards in this PC (virtual: VHDs too, for developer mode).
func List(virtual bool) ([]Card, error) {
	out, err := powershell(listDisks)
	if err != nil {
		return nil, fmt.Errorf("listing disks: %w", err)
	}
	var found []struct {
		Number           int
		Name, Bus        string
		Size             int64
		System, ReadOnly bool
		Removable        bool
		Volumes          []string
	}
	if err := json.Unmarshal(out, &found); err != nil {
		return nil, err
	}
	var disks []Disk
	for _, f := range found {
		id := strconv.Itoa(f.Number)
		bus := f.Bus
		disks = append(disks, Disk{Card: Card{ID: id, Device: `\\.\PhysicalDrive` + id, Name: strings.TrimSpace(f.Name),
			Bus: bus, Size: f.Size, ReadOnly: f.ReadOnly, Virtual: bus == "File Backed Virtual", Volumes: f.Volumes},
			Removable: f.Removable || bus == "SD" || bus == "MMC" || bus == "File Backed Virtual", System: f.System})
	}
	return Offer(disks, virtual), nil
}

// Unmount: on Windows the writer clears the card's partitions itself (with admin rights).
func Unmount(c Card) error { return nil }

// Clear takes every volume off a card (diskpart's clean), so nothing has it open while it's written.
func Clear(c Card) error {
	script, err := os.CreateTemp("", "touchstream-*.txt")
	if err != nil {
		return err
	}
	defer os.Remove(script.Name())
	fmt.Fprintf(script, "select disk %s\r\nattributes disk clear readonly noerr\r\nclean\r\nrescan\r\n", c.ID)
	script.Close()
	cmd := exec.Command("diskpart.exe", "/s", script.Name())
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("couldn't clear the card: %s", strings.TrimSpace(string(out)))
	}
	return nil
}

// Open opens a card to write (as admin).
func Open(c Card) (Target, error) {
	if err := Clear(c); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(c.Device, os.O_RDWR, 0)
	if errors.Is(err, os.ErrNotExist) {
		return nil, ErrGone
	}
	if err != nil {
		return nil, err
	}
	return f, nil
}

// Writable: writing a disk on Windows always takes admin rights.
func Writable(c Card) bool { return false }

// Eject: Windows has no command for it; the installer says when the card can come out.
func Eject(c Card) error { return nil }
