// Package assets holds what the spoke shows: the brand fonts and mark, and the phone page for a
// recording.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package assets

import "embed"

//go:embed files
var files embed.FS

// Read returns one of the files, by name (e.g. "Bricolage-Bold.ttf").
func Read(name string) ([]byte, error) { return files.ReadFile("files/" + name) }
