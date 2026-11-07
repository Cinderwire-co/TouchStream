//go:build pprof

// Built with -tags pprof: Go's profiler on port 6060, for finding what's using the box's CPU.
package main

import (
	"log"
	"net/http"
	_ "net/http/pprof"
)

func init() {
	go func() { log.Print(http.ListenAndServe(":6060", nil)) }()
}
