// Package hubsetup finds the hub on this computer and installs (or updates) it, with the same
// scripts as the one-line install in docs/hub.md, shown as steps instead of a terminal.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package hubsetup

import (
	"bufio"
	"context"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"
)

// Copies of hub/install/macos.sh and windows.ps1 (a test checks they match).
//
//go:embed scripts
var scripts embed.FS

// Found is the hub on this computer, if there is one.
type Found struct {
	Installed   bool   `json:"installed"` // set up to start with the computer
	Running     bool   `json:"running"`
	URL         string `json:"url,omitempty"` // http://localhost:8080
	Version     string `json:"version,omitempty"`
	SetupNeeded bool   `json:"setup_needed"` // its dashboard password hasn't been chosen yet
}

var client = &http.Client{Timeout: 2 * time.Second}

// Probe asks a hub at url about itself.
func Probe(ctx context.Context, url string) (Found, error) {
	req, _ := http.NewRequestWithContext(ctx, "GET", strings.TrimRight(url, "/")+"/api/state", nil)
	resp, err := client.Do(req)
	if err != nil {
		return Found{}, err
	}
	defer resp.Body.Close()
	var state struct {
		Version     string `json:"version"`
		SetupNeeded bool   `json:"setup_needed"`
	}
	if resp.StatusCode != 200 || json.NewDecoder(io.LimitReader(resp.Body, 1<<16)).Decode(&state) != nil || state.Version == "" {
		return Found{}, errors.New("not a TouchStream hub")
	}
	return Found{Running: true, URL: strings.TrimRight(url, "/"), Version: state.Version, SetupNeeded: state.SetupNeeded}, nil
}

// Find looks for the hub on this computer: installed (the scripts' start-up entry), and running on
// one of the ports the scripts choose from (8080, or the next free one).
func Find(ctx context.Context) Found {
	found := Found{Installed: installed()}
	for port := 8080; port <= 8099; port++ {
		if f, err := Probe(ctx, fmt.Sprintf("http://localhost:%d", port)); err == nil {
			f.Installed = found.Installed
			return f
		}
	}
	return found
}

func installed() bool {
	home, _ := os.UserHomeDir()
	switch runtime.GOOS {
	case "darwin":
		_, err := os.Stat(filepath.Join(home, "Library/LaunchAgents/co.cinderwire.touchstream-hub.plist"))
		return err == nil
	case "windows":
		_, err := os.Stat(filepath.Join(os.Getenv("LOCALAPPDATA"), "TouchStream Hub", "bin", "mediamtx.exe"))
		return err == nil
	}
	return false
}

// NeedsHomebrew: on a Mac the hub's FFmpeg comes from Homebrew, whose own installer needs a terminal;
// with neither here, Homebrew has to be installed first (with its .pkg, which needs no terminal).
func NeedsHomebrew() bool {
	if runtime.GOOS != "darwin" {
		return false
	}
	for _, path := range []string{"/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/brew", "/usr/local/bin/brew"} {
		if _, err := os.Stat(path); err == nil {
			return false
		}
	}
	return true
}

// HomebrewPackage is where Homebrew's installer for Macs is.
const HomebrewPackage = "https://github.com/Homebrew/brew/releases/latest"

// Line is one line of the install's output: a step ("Installing FFmpeg") or detail.
type Line struct {
	Text string `json:"text"`
	Step bool   `json:"step"`
}

var (
	stepLine = regexp.MustCompile(`^==> (.+)$`)
	address  = regexp.MustCompile(`http://localhost:(\d+)`)
	colors   = regexp.MustCompile(`\x1b\[[0-9;]*m`)
)

// Install runs the install script for this computer, calling each with its output. source, if set,
// installs the hub from a folder or URL instead of GitHub (for developers). It returns the
// dashboard's address.
func Install(ctx context.Context, source string, each func(Line)) (string, error) {
	dir, err := os.MkdirTemp("", "touchstream-install-")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(dir)
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		script := filepath.Join(dir, "macos.sh")
		if err := copyScript("scripts/macos.sh", script); err != nil {
			return "", err
		}
		cmd = exec.CommandContext(ctx, "/bin/bash", script)
	case "windows":
		script := filepath.Join(dir, "windows.ps1")
		if err := copyScript("scripts/windows.ps1", script); err != nil {
			return "", err
		}
		cmd = exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script)
		hideWindow(cmd)
	default:
		return "", errors.New("the installer is for macOS and Windows: on Linux, use the one-line install in docs/hub.md")
	}
	cmd.Env = append(os.Environ(), "NONINTERACTIVE=1", "TOUCHSTREAM_NO_OPEN=1")
	if source != "" {
		cmd.Env = append(cmd.Env, "TOUCHSTREAM_SOURCE="+source)
	}
	cmd.Stdin = nil // nothing to type into: a prompt gets end-of-input instead of waiting forever
	out, err := cmd.StdoutPipe()
	if err != nil {
		return "", err
	}
	cmd.Stderr = cmd.Stdout
	if err := cmd.Start(); err != nil {
		return "", err
	}
	var url, last string
	lines := bufio.NewScanner(out)
	for lines.Scan() {
		text := strings.TrimSpace(colors.ReplaceAllString(lines.Text(), ""))
		if text == "" {
			continue
		}
		if m := address.FindStringSubmatch(text); m != nil {
			url = "http://localhost:" + m[1]
		}
		if m := stepLine.FindStringSubmatch(text); m != nil {
			each(Line{Text: m[1], Step: true})
		} else {
			each(Line{Text: text})
		}
		last = text
	}
	if err := cmd.Wait(); err != nil {
		return "", fmt.Errorf("the hub didn't install: %s", strings.TrimPrefix(last, "Error: "))
	}
	if url == "" {
		url = Find(ctx).URL
	}
	if url == "" {
		return "", errors.New("the hub installed, but didn't start")
	}
	return url, nil
}

func copyScript(name, to string) error {
	data, err := scripts.ReadFile(name)
	if err != nil {
		return err
	}
	return os.WriteFile(to, data, 0o755)
}
