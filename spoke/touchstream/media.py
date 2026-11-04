"""Capture, preview, encoding, recording and the SRT uplink (GStreamer)

One capture pipeline feeds the touchscreen preview and, when the hub asks for
video, the Pi 4's hardware H.264 encoder. The encoded stream is muxed to MPEG-TS
and sent to a local UDP port, and optionally recorded in 5-minute segments.
A separate uplink pipeline relays that UDP stream to the hub over SRT, so a
dropped internet connection never interrupts capture or the local recording.

Sources: 'hdmi' (the capture card), 'slate' (branded "Waiting for source"
holding screen with a pulsing ember core, sent when there's no HDMI signal so
viewers never see a dead stream) and 'bars' (colour bars, timecode and a
1 kHz tone). TOUCHSTREAM_TEST_SOURCE=1 pretends an HDMI source is connected
and shows bars, for development without capture hardware.
"""
import glob
import os
import re
import subprocess
import threading
import time

from . import ASSETS_DIR
from .config import CAPTURE_WIDTH, CAPTURE_HEIGHT

try:
    import gi
    gi.require_version('Gst', '1.0')
    gi.require_version('GstController', '1.0')
    from gi.repository import Gst, GstController
    GST_OK = True
except Exception as e:
    print("GStreamer (python gi) not available:", e)
    GST_OK = False

TEST_SOURCE = os.environ.get('TOUCHSTREAM_TEST_SOURCE') == '1'
# dsnoop lets the level meter and the encoder share the HDMI audio input;
# by name because the card number varies between boots
AUDIO_DEVICE = 'dsnoop:CARD=tc358743,DEV=0'
UPLINK_PORT = 5600            # local UDP hop between the encoder and the SRT uplink
SEGMENT_SECONDS = 300         # local recording segment length
SLATE = os.path.join(ASSETS_DIR, 'slate', 'slate.png')
SLATE_CORE = os.path.join(ASSETS_DIR, 'slate', 'core.png')
SLATE_CORE_POS = (960 - 170, 460 - 170)   # matches tools/render-brand-assets.py

# Preview resolution (scaled down AGGRESSIVELY for performance)
# 480x270 = 1/16th the pixels of 1080p, much faster
PREVIEW_WIDTH = 480
PREVIEW_HEIGHT = 270


def find_video_device():
    """The unicam capture node fed by the TC358743 (usually /dev/video0)."""
    for path in sorted(glob.glob('/sys/class/video4linux/video*')):
        try:
            with open(os.path.join(path, 'name')) as f:
                if 'unicam' in f.read():
                    return '/dev/' + os.path.basename(path)
        except OSError:
            pass
    return '/dev/video0'


VIDEO_DEVICE = find_video_device()


def rate_fraction(fps):
    """Frames per second -> the exact fraction sources use (29.97 is 30000/1001)."""
    for whole in (24, 30, 60):
        if abs(fps - whole * 1000 / 1001) < 0.02:
            return whole * 1000, 1001
    return round(fps), 1


def probe_signal():
    """Return (width, height, fps) of the incoming HDMI signal, or None if there is none."""
    if TEST_SOURCE:
        return CAPTURE_WIDTH, CAPTURE_HEIGHT, 30.0
    try:
        out = subprocess.run(['v4l2-ctl', '-d', VIDEO_DEVICE, '--query-dv-timings'],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    num = lambda key: int(m.group(1)) if (m := re.search(rf'{key}:\s*(\d+)', out.stdout)) else 0
    w, h = num('Active width'), num('Active height')
    total = num('Total width') * num('Total height')
    if not w or not h or not total:
        return None
    return w, h, num('Pixelclock') / total


def lock_signal():
    """Apply the detected timings and the capture format before (re)starting capture."""
    if TEST_SOURCE:
        return
    subprocess.run(['v4l2-ctl', '-d', VIDEO_DEVICE, '--set-dv-bt-timings', 'query'],
                   capture_output=True, timeout=5)
    subprocess.run(['v4l2-ctl', '-d', VIDEO_DEVICE,
                    f'--set-fmt-video=width={CAPTURE_WIDTH},height={CAPTURE_HEIGHT},pixelformat=UYVY'],
                   capture_output=True, timeout=5)


def _video_source(source, rate):
    if TEST_SOURCE and source == 'hdmi':
        source = 'bars'
    caps = f"video/x-raw,format=UYVY,width={CAPTURE_WIDTH},height={CAPTURE_HEIGHT},framerate={rate[0]}/{rate[1]}"
    if source == 'bars':
        return (f"videotestsrc is-live=true pattern=smpte ! {caps} ! timecodestamper ! "
                "timeoverlay time-mode=time-code font-desc=\"Sans Bold 54\" valignment=bottom halignment=center")
    if source == 'slate':
        # Convert the still once, repeat it at the frame rate, then blend the core on top
        return (f"filesrc location=\"{SLATE}\" ! pngdec ! videoconvert ! videoscale ! "
                f"video/x-raw,format=UYVY,width={CAPTURE_WIDTH},height={CAPTURE_HEIGHT} ! "
                f"imagefreeze is-live=true ! {caps} ! gdkpixbufoverlay name=core "
                f"location=\"{SLATE_CORE}\" offset-x={SLATE_CORE_POS[0]} offset-y={SLATE_CORE_POS[1]}")
    # Whatever rate the source sends (e.g. 29.97 for a "30" setting); bt601 is what the TC358743 delivers
    return (f"v4l2src device={VIDEO_DEVICE} ! "
            f"video/x-raw,format=UYVY,width={CAPTURE_WIDTH},height={CAPTURE_HEIGHT},colorimetry=bt601")


def _audio_source(source, for_stream=False):
    if TEST_SOURCE and source == 'hdmi':
        source = 'bars'
    if source == 'bars':
        return "audiotestsrc is-live=true wave=sine freq=1000 volume=0.1"   # -20 dBFS
    if source == 'slate':
        return "audiotestsrc is-live=true wave=silence"
    if for_stream:
        # Mixed over silence: HDMI without audio (DVI adapters, muted sources) gives the
        # capture card no audio clock, and a bare ALSA source would stall the whole stream
        return ("audiomixer name=amix latency=200000000  "
                "audiotestsrc is-live=true wave=silence ! audio/x-raw,rate=48000,channels=2 ! amix.  "
                f"alsasrc device={AUDIO_DEVICE} ! audioconvert ! audioresample ! audio/x-raw,rate=48000,channels=2 ! "
                "queue leaky=downstream max-size-time=500000000 ! amix.  amix.")
    return f"alsasrc device={AUDIO_DEVICE}"


def _set_null(pipeline, what):
    """Stop a pipeline without risking a hang: with no HDMI signal the capture card
    gives no audio clock, and stopping an ALSA source then blocks forever."""
    worker = threading.Thread(target=pipeline.set_state, args=(Gst.State.NULL,), daemon=True)
    worker.start()
    worker.join(3)
    if worker.is_alive():
        print(f"{what} did not stop cleanly; continuing without it")


def _pulse(element):
    """Breathe the slate's ember core: alpha follows a 2-second sine between 0.35 and 1."""
    lfo = GstController.LFOControlSource(waveform=GstController.LFOWaveform.SINE,
                                         frequency=0.5, amplitude=0.325, offset=0.675)
    element.add_control_binding(GstController.DirectControlBinding.new(element, 'alpha', lfo))


class CapturePipeline:
    """HDMI in -> preview appsink, plus (optionally) H.264/AAC out to UDP and local files.

    source: 'hdmi', 'slate' or 'bars'
    rate:   (numerator, denominator) frame rate: what HDMI is sending, or what to generate
    encode: None for preview only, or {'bitrate_kbps': int, 'record_dir': str|None}
    """

    def __init__(self, on_frame_callback, on_error=None):
        self.on_frame = on_frame_callback
        self.on_error = on_error
        self.pipeline = None
        self.encode = None
        self.source = 'hdmi'
        self.rate = (30, 1)
        self.running = False
        self.started_at = 0
        if not GST_OK:
            raise RuntimeError("GStreamer not available")

    def describe(self):
        parts = [
            f"{_video_source(self.source, self.rate)} ! tee name=t",
            # The touchscreen preview never needs more than 30 fps
            "t. ! queue leaky=downstream max-size-buffers=2 ! videorate drop-only=true max-rate=30 ! "
            "videoscale add-borders=false ! "
            f"video/x-raw,width={PREVIEW_WIDTH},height={PREVIEW_HEIGHT} ! "
            "videoconvert n-threads=4 ! video/x-raw,format=RGB ! "
            "appsink name=preview emit-signals=true max-buffers=1 drop=true sync=false",
        ]
        if self.encode:
            bps = self.encode['bitrate_kbps'] * 1000
            fps = self.rate[0] / self.rate[1]
            # Pi 4 hardware encoder: High profile, keyframe every 2 s and SPS/PPS on every
            # keyframe (what YouTube/Facebook expect; lets a viewer join mid-stream).
            # The level must be explicit or it negotiates one too low for 1080p: 4 covers
            # 1080p30, 4.2 is needed for 1080p50 (measured: the encoder manages ~72 fps).
            # Its constant-bitrate mode fails with High profile, so this targets the bitrate (VBR).
            parts += [
                "t. ! queue leaky=downstream max-size-buffers=4 ! "
                f"v4l2h264enc extra-controls=\"controls,video_bitrate={bps},"
                f"h264_i_frame_period={round(fps * 2)},repeat_sequence_header=1\" ! "
                f"video/x-h264,profile=(string)high,level=(string){'4.2' if fps > 31 else '4'} ! "
                "h264parse config-interval=-1 ! tee name=vt",
                f"{_audio_source(self.source, for_stream=True)} ! queue ! audioconvert ! audioresample ! "
                "audio/x-raw,rate=48000,channels=2 ! avenc_aac bitrate=192000 ! aacparse ! tee name=at",
                "mpegtsmux name=mux alignment=7 latency=100000000 ! "
                f"udpsink host=127.0.0.1 port={UPLINK_PORT} sync=false async=false",
                "vt. ! queue ! mux.",
                "at. ! queue ! mux.",
            ]
            if self.encode.get('record_dir'):
                location = os.path.join(self.encode['record_dir'], 'part-%05d.ts')
                parts += [
                    f"splitmuxsink name=rec muxer-factory=mpegtsmux "
                    f"max-size-time={SEGMENT_SECONDS * Gst.SECOND} location=\"{location}\"",
                    "vt. ! queue ! rec.video",
                    "at. ! queue ! rec.audio_%u",
                ]
        return '  '.join(parts)

    def build_pipeline(self):
        desc = self.describe()
        print(f"Building pipeline: {desc}")
        self.pipeline = Gst.parse_launch(desc)
        self.pipeline.get_by_name('preview').connect('new-sample', self._on_new_sample)
        if self.source == 'slate':
            _pulse(self.pipeline.get_by_name('core'))
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message::error', self._on_bus_error)

    def _on_bus_error(self, bus, message):
        err, debug = message.parse_error()
        print(f"Capture pipeline error: {err.message} ({debug})")
        if self.on_error:
            self.on_error(err.message)

    def _on_new_sample(self, sink):
        sample = sink.emit('pull-sample')
        if sample:
            buf = sample.get_buffer()
            data = buf.extract_dup(0, buf.get_size())
            self.on_frame(data, PREVIEW_WIDTH, PREVIEW_HEIGHT)
        return Gst.FlowReturn.OK

    def start(self, encode=None, source='hdmi', rate=(30, 1)):
        self.encode = encode
        self.source = source
        self.rate = rate
        if not self.pipeline:
            self.build_pipeline()
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            print("Failed to start pipeline!")
            return False
        self.running = True
        self.started_at = time.time()
        print(f"Capture started: {source} at {rate[0] / rate[1]:.2f} fps, "
              f"{'encoding ' + str(encode['bitrate_kbps']) + ' kbps' if encode else 'preview only'}")
        return True

    def stop(self):
        self.running = False
        if self.pipeline:
            if self.encode and self.encode.get('record_dir'):
                # EOS lets splitmuxsink finish the segment it is writing
                self.pipeline.send_event(Gst.Event.new_eos())
                self.pipeline.get_bus().timed_pop_filtered(3 * Gst.SECOND, Gst.MessageType.EOS)
            _set_null(self.pipeline, 'Capture')
            self.pipeline = None
            print("Capture stopped")

    def restart(self, encode=None, source='hdmi', rate=(30, 1)):
        self.stop()
        return self.start(encode, source, rate)


class Uplink:
    """Relays the local MPEG-TS stream to the hub over SRT; retried on failure.

    connect() sets where to send and when: after a capture (re)start the link waits a
    moment, so the hub's first look at the stream already lists both video and audio
    (the muxer announces whichever starts first, and the hub only reads the first table).
    """

    def __init__(self):
        self.pipeline = None
        self.target = None
        self.error = None
        self.started_at = None
        self.connect_at = 0

    def connect(self, stream, delay=0):
        self._stop_pipeline()
        self.target = stream
        self.connect_at = time.time() + delay

    def disconnect(self):
        self._stop_pipeline()
        self.target = None

    def _start_pipeline(self):
        stream = self.target
        uri = (f"srt://{stream['host']}:{stream['port']}?mode=caller"
               f"&latency={stream.get('latency_ms', 1000)}")
        self.pipeline = Gst.Pipeline.new('uplink')
        src = Gst.ElementFactory.make('udpsrc')
        src.set_property('port', UPLINK_PORT)
        src.set_property('buffer-size', 4 * 1024 * 1024)
        src.set_property('caps', Gst.Caps.from_string('video/mpegts,systemstream=(boolean)true,packetsize=(int)188'))
        sink = Gst.ElementFactory.make('srtsink')
        sink.set_property('uri', uri)
        sink.set_property('streamid', stream['streamid'])
        sink.set_property('wait-for-connection', False)
        sink.set_property('sync', False)
        self.pipeline.add(src)
        self.pipeline.add(sink)
        src.link(sink)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message::error', self._on_error)
        self.error = None
        self.started_at = time.time()
        self.pipeline.set_state(Gst.State.PLAYING)
        print(f"Uplink to {stream['host']}:{stream['port']} started")

    def _on_error(self, bus, message):
        err, _ = message.parse_error()
        self.error = err.message
        print(f"Uplink error: {err.message}")

    def _stop_pipeline(self):
        if self.pipeline:
            _set_null(self.pipeline, 'Uplink')
            self.pipeline = None

    def stop(self):
        self.disconnect()

    def check(self):
        """Called every second: connect when due, and retry 5 s after an error."""
        if self.pipeline and self.error and time.time() - self.started_at > 5:
            self._stop_pipeline()
        if self.target and not self.pipeline and time.time() >= self.connect_at:
            self._start_pipeline()

    def state(self):
        if not self.target:
            return 'off'
        if not self.pipeline or self.error:
            return 'retrying' if self.error else 'connecting'
        return 'sending'


# ---- Audio Monitor (captures HDMI audio for level metering only) ----
class AudioMonitor:
    def __init__(self, on_level_callback):
        self.on_level = on_level_callback
        self.pipeline = None
        self.running = False
        if not GST_OK:
            raise RuntimeError("GStreamer not available")

    def build_pipeline(self):
        # Capture from ALSA, analyze levels, discard audio (no speaker on Pi)
        # level element provides RMS/peak levels for metering
        pipeline_str = (
            f"{_audio_source('hdmi')} ! "
            "audioconvert ! "
            "level name=level interval=50000000 ! "  # 50ms intervals
            "fakesink"  # Discard audio - no speaker on Pi
        )
        print(f"Building audio pipeline: {pipeline_str}")
        self.pipeline = Gst.parse_launch(pipeline_str)

        # Connect to bus for level messages
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message::element', self._on_level_message)

    def _on_level_message(self, bus, message):
        struct = message.get_structure()
        if struct and struct.get_name() == 'level':
            # Extract RMS levels for left and right channels
            try:
                rms = struct.get_value('rms')
                if rms:
                    if hasattr(rms, '__len__') and len(rms) >= 2:
                        left_db = float(rms[0])
                        right_db = float(rms[1])
                    elif hasattr(rms, '__len__') and len(rms) >= 1:
                        left_db = right_db = float(rms[0])  # Mono
                    else:
                        left_db = right_db = float(rms)

                    # Convert from dB to linear (0-1 range): -60dB = 0, 0dB = 1
                    left = max(0, min(1, (left_db + 60) / 60))
                    right = max(0, min(1, (right_db + 60) / 60))
                    self.on_level(left, right)
            except Exception as e:
                print(f"Audio level parse error: {e}")

    def start(self):
        if not self.pipeline:
            self.build_pipeline()
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            print("Failed to start audio pipeline!")
            return False
        self.running = True
        print("Audio monitor started")
        return True

    def stop(self):
        self.running = False
        if self.pipeline:
            _set_null(self.pipeline, 'Audio monitor')
            self.pipeline = None
            print("Audio monitor stopped")
