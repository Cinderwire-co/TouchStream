//go:build !linux

package install

import (
	"errors"
	"os"
)

var errNotHere = errors.New("only on the box")

func makeDataPartition(emmc string, at int64, settings string) error { return errNotHere }
func rereadPartitions(f *os.File) error                              { return errNotHere }
func stillRoot() (func(), error)                                     { return nil, errNotHere }
func run(name string, args ...string) error                          { return errNotHere }
