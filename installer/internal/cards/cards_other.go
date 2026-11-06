//go:build !darwin && !windows

package cards

import "errors"

var errUnsupported = errors.New("writing spoke cards works on macOS and Windows")

func List(virtual bool) ([]Card, error) { return nil, errUnsupported }
func Unmount(c Card) error              { return errUnsupported }
func Open(c Card) (Target, error)       { return nil, errUnsupported }
func Writable(c Card) bool              { return false }
func Eject(c Card) error                { return nil }
