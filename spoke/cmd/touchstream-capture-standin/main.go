// A stand-in for the PicoKVM's capture helper (touchstream-capture), for the emulator: it plays
// an MPEG-TS clip's H.264 on stdout in real time, over and over, and says what's "coming in" on
// stderr, as the helper does. SIGUSR1 pulls the HDMI cable out and SIGUSR2 puts it back.
//
//	touchstream-capture-standin CLIP.ts [-fps N] [-bitrate KBPS]
//
// (-fps and -bitrate are what the spoke asks for; a clip is what it is.)
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/ts"
)

var plugged atomic.Bool

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: touchstream-capture-standin CLIP.ts [-fps N] [-bitrate KBPS]")
		os.Exit(2)
	}
	clip := os.Args[1]
	flags := flag.NewFlagSet("standin", flag.ExitOnError)
	flags.String("fps", "30", "the frame rate asked for")
	flags.Int("bitrate", 8000, "the bitrate asked for")
	_ = flags.Parse(os.Args[2:])

	plugged.Store(true)
	cable := make(chan os.Signal, 4)
	signal.Notify(cable, syscall.SIGUSR1, syscall.SIGUSR2)
	say(true)
	go func() {
		for s := range cable {
			in := s == syscall.SIGUSR2
			if plugged.Swap(in) != in {
				say(in)
			}
		}
	}()

	out := bufio.NewWriterSize(os.Stdout, 256<<10)
	var start time.Time
	var offset, first int64 = 0, -1 // 90 kHz: how far earlier loops ran, and the first frame's time
	waitKey := false                // after the cable goes back in, the picture starts at a keyframe
	for {
		end, err := play(clip, func(dts int64, frame []byte) {
			t := dts + offset
			if first < 0 {
				first, start = t, time.Now()
			}
			time.Sleep(time.Until(start.Add(time.Duration((t - first) * int64(time.Second) / ts.PTSHz))))
			if !plugged.Load() {
				waitKey = true
				return
			}
			if waitKey && !keyframe(frame) {
				return
			}
			waitKey = false
			if _, err := out.Write(frame); err != nil || out.Flush() != nil {
				os.Exit(0) // the spoke stopped reading
			}
		})
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		offset += end
	}
}

// say tells the spoke what's coming in: the clip's own format (as the helper reports the source's).
func say(in bool) {
	status := map[string]any{"signal": false}
	if in {
		status = map[string]any{"signal": true, "width": 1920, "height": 1080, "fps": 30}
	}
	line, _ := json.Marshal(status)
	fmt.Fprintln(os.Stderr, string(line))
}

// play sends each of the clip's video frames (a whole PES each), with its decode time, and returns
// how long the clip runs (90 kHz), to the end of its last frame.
func play(path string, frame func(dts int64, data []byte)) (int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	in := bufio.NewReaderSize(f, 64<<10)
	var tables ts.Tables
	var data []byte
	pts, firstPTS, lastPTS, step := int64(-1), int64(-1), int64(0), int64(ts.PTSHz/30)
	flush := func() {
		if pts >= 0 && len(data) > 0 {
			frame(pts, data)
		}
		data = nil
	}
	p := make([]byte, ts.PacketSize)
	for {
		if _, err := io.ReadFull(in, p); errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			break
		} else if err != nil {
			return 0, err
		}
		tables.Note(p)
		if tables.VideoPID == 0 || ts.PID(p) != tables.VideoPID {
			continue
		}
		payload := ts.Payload(p)
		if ts.Start(p) {
			flush()
			if t, ok := ts.DTS(p); ok {
				if firstPTS < 0 {
					firstPTS = t
				} else if t > lastPTS {
					step = min(step, t-lastPTS)
				}
				pts, lastPTS = t, max(lastPTS, t)
			}
			if len(payload) < 9 {
				continue
			}
			payload = payload[9+int(payload[8]):] // past the PES header
		}
		data = append(data, payload...)
	}
	flush()
	if firstPTS < 0 {
		return 0, fmt.Errorf("%s has no H.264 to play", path)
	}
	return lastPTS + step - firstPTS, nil
}

// keyframe reports whether a frame has an IDR picture (NAL type 5).
func keyframe(frame []byte) bool {
	for i := 0; i+3 < len(frame); i++ {
		if frame[i] == 0 && frame[i+1] == 0 && frame[i+2] == 1 && frame[i+3]&0x1F == 5 {
			return true
		}
	}
	return false
}
