//go:build !windows

package elevate

import "errors"

func Run(exe string, args ...string) error {
	return errors.New("writing spoke cards works on macOS and Windows")
}
