/*
 * touchstream-capture: the PicoKVM's HDMI input as H.264, for the TouchStream spoke.
 *
 *   touchstream-capture [-fps N] [-bitrate KBPS]
 *
 * The TC358743's picture (V4L2: /dev/video0, YUYV) goes in DMA buffers straight to the RV1106's
 * encoder, and out on stdout as H.264: Main profile, no B-frames, a constant bitrate, and a
 * keyframe every 2 seconds with its SPS and PPS. What's coming in goes on stderr, a JSON line each
 * time it changes:
 *
 *   {"signal": true, "width": 1920, "height": 1080, "fps": 59.94}
 *   {"signal": false}
 *
 * -fps (24, 25, 30, 50 or 60) goes in the EDID the source reads: 1080p at that rate, first, with
 * 2-channel PCM sound, as an HDMI sink (a DVI one gets no sound). See spoke/docs/picokvm.md.
 *
 * Built with Luckfox's SDK toolchain against Rockchip's MPI (spoke/capture/build). It's ours, not
 * Luckfox's kvm_video, which showed which devices and calls the box uses.
 *
 * Copyright (c) 2025 Will Reeves and TouchStream Contributors
 * Licensed under the MIT License - see LICENSE file for details
 */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#ifndef TOUCHSTREAM_EDID_TEST
#include <linux/videodev2.h>
#include "rk_comm_mb.h"
#include "rk_comm_venc.h"
#include "rk_mpi_mb.h"
#include "rk_mpi_sys.h"
#include "rk_mpi_venc.h"
#endif

#define VIDEO_DEV "/dev/video0"
#define SUBDEV "/dev/v4l-subdev2" /* the TC358743: timings, source changes, the EDID */
#define BUFFERS 4
#define CHANNEL 0

/* ---- The EDID: what the source reads to choose what to send ---- */

struct timing { int fps, vic, clock10k, hblank, hfront; }; /* CEA-861 1080p timings */
static const struct timing timings[] = {
    {60, 16, 14850, 280, 88}, {50, 31, 14850, 720, 528}, {30, 34, 7425, 280, 88},
    {25, 33, 7425, 720, 528}, {24, 32, 7425, 830, 638},
};

static const struct timing *timing_for(int fps) {
    for (size_t i = 0; i < sizeof timings / sizeof *timings; i++)
        if (timings[i].fps == fps) return &timings[i];
    return &timings[2]; /* 30 */
}

static void checksum(uint8_t *block) {
    uint8_t sum = 0;
    for (int i = 0; i < 127; i++) sum += block[i];
    block[127] = (uint8_t)(256 - sum);
}

/* An 18-byte detailed timing for 1920x1080 at t: H and V sync positive, 4/5/36 vertical. */
static void detailed(uint8_t *d, const struct timing *t) {
    int hsync = 44, vblank = 45, vfront = 4, vsync = 5;
    d[0] = t->clock10k & 0xFF, d[1] = t->clock10k >> 8;
    d[2] = 1920 & 0xFF, d[3] = t->hblank & 0xFF, d[4] = (1920 >> 8) << 4 | t->hblank >> 8;
    d[5] = 1080 & 0xFF, d[6] = vblank, d[7] = (1080 >> 8) << 4;
    d[8] = t->hfront & 0xFF, d[9] = hsync, d[10] = vfront << 4 | vsync;
    d[11] = (t->hfront >> 8) << 6;
    d[12] = 800 & 0xFF, d[13] = 450 & 0xFF, d[14] = (800 >> 8) << 4 | 450 >> 8; /* 800 x 450 mm */
    d[17] = 0x1E; /* digital separate sync, both positive */
}

static void text(uint8_t *d, uint8_t tag, const char *s) {
    d[3] = tag;
    size_t n = strlen(s);
    for (int i = 0; i < 13; i++) d[5 + i] = i < (int)n ? (uint8_t)s[i] : i == (int)n ? '\n' : ' ';
}

/* edid fills out[256]: a base block and a CEA-861 extension, preferring 1080p at fps. */
void edid(uint8_t out[256], int fps) {
    const struct timing *t = timing_for(fps);
    uint8_t *b = out, *x = out + 128;
    memset(out, 0, 256);
    static const uint8_t header[8] = {0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0};
    memcpy(b, header, 8);
    b[8] = 0x52, b[9] = 0x6D;       /* "TSM" */
    b[10] = 0x01, b[11] = 0x00;     /* product 1 */
    b[16] = 1, b[17] = 2025 - 1990; /* made in week 1, 2025 */
    b[18] = 1, b[19] = 3;           /* EDID 1.3 */
    b[20] = 0x80;                   /* digital */
    b[21] = 80, b[22] = 45;         /* 80 x 45 cm */
    b[23] = 120;                    /* gamma 2.2 */
    b[24] = 0x0E;                   /* RGB in sRGB; the first detailed timing is the preferred one */
    static const uint8_t chroma[10] = {0xEE, 0x91, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54}; /* sRGB */
    memcpy(b + 25, chroma, 10);
    b[35] = 0x20; /* 640x480 at 60, which every HDMI sink takes */
    for (int i = 38; i < 54; i++) b[i] = 0x01; /* no standard timings */
    detailed(b + 54, t);
    uint8_t *range = b + 72; /* 23-61 Hz, 15-70 kHz, up to 150 MHz */
    range[3] = 0xFD, range[5] = 23, range[6] = 61, range[7] = 15, range[8] = 70, range[9] = 15, range[11] = 0x0A;
    for (int i = 12; i < 18; i++) range[i] = ' ';
    text(b + 90, 0xFC, "TouchStream");
    b[108 + 3] = 0x10; /* an empty descriptor */
    b[126] = 1;        /* one extension */
    checksum(b);

    /* The CEA-861 extension: the 1080p rates (this one first), PCM sound, and HDMI */
    x[0] = 0x02, x[1] = 0x03;
    x[3] = 0x80 | 0x40 | 0x01; /* underscanned; basic audio; one native timing (the preferred one) */
    int at = 4;
    x[at++] = 0x40 | 6; /* video: the VICs, this rate's first */
    x[at++] = t->vic;
    for (size_t i = 0; i < sizeof timings / sizeof *timings; i++)
        if (timings[i].vic != t->vic) x[at++] = timings[i].vic;
    x[at++] = 1;        /* 640x480 */
    x[at++] = 0x20 | 3; /* audio: 2-channel LPCM at 32, 44.1 and 48 kHz, 16, 20 and 24 bits */
    x[at++] = 1 << 3 | 1, x[at++] = 0x07, x[at++] = 0x07;
    x[at++] = 0x80 | 3; /* speakers: front left and right */
    x[at++] = 0x01, x[at++] = 0, x[at++] = 0;
    x[at++] = 0xE0 | 2; /* video capability: RGB range selectable; IT formats underscanned */
    x[at++] = 0x00, x[at++] = 0x40 | 2 << 2 | 2; /* (CE formats too) */
    x[at++] = 0x60 | 5; /* HDMI (IEEE OUI 00-0C-03), physical address 1.0.0.0 */
    x[at++] = 0x03, x[at++] = 0x0C, x[at++] = 0x00, x[at++] = 0x10, x[at++] = 0x00;
    x[2] = (uint8_t)at; /* where detailed timings would start: none here */
    checksum(x);
}

#ifdef TOUCHSTREAM_EDID_TEST
/* For checking the EDID on a computer: prints it in hex for each rate. */
int main(void) {
    for (size_t i = 0; i < sizeof timings / sizeof *timings; i++) {
        uint8_t e[256];
        edid(e, timings[i].fps);
        printf("%d ", timings[i].fps);
        for (int j = 0; j < 256; j++) printf("%02x", e[j]);
        printf("\n");
    }
    return 0;
}
#else

/* ---- Saying what's coming in ---- */

static void say_signal(int on, unsigned w, unsigned h, double fps) {
    if (on)
        fprintf(stderr, "{\"signal\": true, \"width\": %u, \"height\": %u, \"fps\": %.2f}\n", w, h, fps);
    else
        fprintf(stderr, "{\"signal\": false}\n");
    fflush(stderr);
}

static int xioctl(int fd, unsigned long req, void *arg) {
    int r;
    do r = ioctl(fd, req, arg);
    while (r < 0 && errno == EINTR);
    return r;
}

/* set_edid gives the source our EDID, if it doesn't have it already (setting it makes the source
 * read it again, and start over). */
static void set_edid(int subdev, int fps) {
    uint8_t want[256], have[256];
    edid(want, fps);
    struct v4l2_edid e = {.pad = 0, .start_block = 0, .blocks = 2, .edid = have};
    if (xioctl(subdev, VIDIOC_G_EDID, &e) == 0 && e.blocks == 2 && memcmp(want, have, 256) == 0) return;
    e = (struct v4l2_edid){.pad = 0, .start_block = 0, .blocks = 2, .edid = want};
    if (xioctl(subdev, VIDIOC_S_EDID, &e) < 0) fprintf(stderr, "Couldn't set the EDID: %s\n", strerror(errno));
}

/* locked asks the TC358743 what's coming in, and sets the capture to it. 0: nothing usable. */
static int locked(int subdev, struct v4l2_dv_timings *dv, double *fps) {
    memset(dv, 0, sizeof *dv);
    if (xioctl(subdev, VIDIOC_QUERY_DV_TIMINGS, dv) < 0) return 0; /* ENOLINK, ENOLCK, ERANGE */
    const struct v4l2_bt_timings *bt = &dv->bt;
    double total = (double)(bt->width + bt->hfrontporch + bt->hsync + bt->hbackporch) *
                   (bt->height + bt->vfrontporch + bt->vsync + bt->vbackporch);
    if (bt->width == 0 || bt->height == 0 || bt->pixelclock == 0 || total == 0) return 0;
    *fps = bt->pixelclock / total;
    if (xioctl(subdev, VIDIOC_S_DV_TIMINGS, dv) < 0) {
        fprintf(stderr, "Couldn't set the capture's timings: %s\n", strerror(errno));
        return 0;
    }
    return 1;
}

/* ---- H.264 out: each keyframe with its SPS and PPS ---- */

static uint8_t params[512]; /* the latest SPS and PPS, with their start codes */
static size_t params_len;

static int nal_type_at(const uint8_t *p, size_t n, size_t i) { return i + 3 < n ? p[i + 3] & 0x1F : -1; }

/* write_frame writes one encoded frame, putting the SPS and PPS before a keyframe that lacks them
 * (the encoder may give them only once). It returns -1 when the spoke has stopped reading. */
static int write_frame(const uint8_t *p, size_t n) {
    int idr = 0, sps = 0;
    size_t first_param = n, params_end = 0;
    for (size_t i = 0; i + 3 < n; i++) {
        if (p[i] || p[i + 1] || p[i + 2] != 1) continue;
        int type = nal_type_at(p, n, i);
        size_t start = i > 0 && p[i - 1] == 0 ? i - 1 : i;
        if (type == 7 || type == 8) {
            if (type == 7) sps = 1;
            if (start < first_param) first_param = start;
        } else if (first_param < n && params_end == 0) {
            params_end = start; /* the parameters end where the next NAL starts */
        }
        if (type == 5) idr = 1;
    }
    if (sps && params_end > first_param && params_end - first_param <= sizeof params) {
        memcpy(params, p + first_param, params_end - first_param);
        params_len = params_end - first_param;
    }
    if (idr && !sps && params_len && fwrite(params, 1, params_len, stdout) != params_len) return -1;
    if (fwrite(p, 1, n, stdout) != n || fflush(stdout) != 0) return -1;
    return 0;
}

/* ---- Capture and encoding, for one signal ---- */

struct capture {
    int video;
    MB_POOL pool;
    MB_BLK blk[BUFFERS];
    int fd[BUFFERS];
    unsigned width, height;
    int encoding;
};

static void stop_capture(struct capture *c) {
    if (c->video >= 0) {
        enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
        xioctl(c->video, VIDIOC_STREAMOFF, &type);
        struct v4l2_requestbuffers req = {.count = 0, .type = type, .memory = V4L2_MEMORY_DMABUF};
        xioctl(c->video, VIDIOC_REQBUFS, &req);
        close(c->video);
        c->video = -1;
    }
    if (c->encoding) {
        RK_MPI_VENC_StopRecvFrame(CHANNEL);
        RK_MPI_VENC_DestroyChn(CHANNEL);
        c->encoding = 0;
    }
    for (int i = 0; i < BUFFERS; i++)
        if (c->blk[i]) RK_MPI_MB_ReleaseMB(c->blk[i]), c->blk[i] = NULL;
    if (c->pool != MB_INVALID_POOLID) RK_MPI_MB_DestroyPool(c->pool), c->pool = MB_INVALID_POOLID;
}

static int start_encoder(unsigned w, unsigned h, double fps, int kbps) {
    unsigned rate = (unsigned)(fps + 0.5);
    VENC_CHN_ATTR_S attr;
    memset(&attr, 0, sizeof attr);
    attr.stVencAttr.enType = RK_VIDEO_ID_AVC;
    attr.stVencAttr.enPixelFormat = RK_FMT_YUV422_YUYV;
    attr.stVencAttr.u32Profile = H264E_PROFILE_MAIN;
    attr.stVencAttr.u32PicWidth = w, attr.stVencAttr.u32PicHeight = h;
    attr.stVencAttr.u32VirWidth = (w + 1) & ~1u, attr.stVencAttr.u32VirHeight = (h + 1) & ~1u;
    attr.stVencAttr.u32MaxPicWidth = w, attr.stVencAttr.u32MaxPicHeight = h;
    attr.stVencAttr.u32StreamBufCnt = 4;
    attr.stVencAttr.u32BufSize = w * h * 3 / 4; /* room for a keyframe at the highest bitrate */
    attr.stRcAttr.enRcMode = VENC_RC_MODE_H264CBR;
    attr.stRcAttr.stH264Cbr.u32Gop = rate * 2; /* a keyframe every 2 seconds */
    attr.stRcAttr.stH264Cbr.u32BitRate = (RK_U32)kbps;
    attr.stRcAttr.stH264Cbr.u32SrcFrameRateNum = rate, attr.stRcAttr.stH264Cbr.u32SrcFrameRateDen = 1;
    attr.stRcAttr.stH264Cbr.fr32DstFrameRateNum = rate, attr.stRcAttr.stH264Cbr.fr32DstFrameRateDen = 1;
    attr.stRcAttr.stH264Cbr.u32StatTime = 3;
    attr.stGopAttr.enGopMode = VENC_GOPMODE_NORMALP; /* I and P frames only */
    if (RK_MPI_VENC_CreateChn(CHANNEL, &attr) != RK_SUCCESS) return -1;
    VENC_RECV_PIC_PARAM_S recv = {.s32RecvPicNum = -1};
    if (RK_MPI_VENC_StartRecvFrame(CHANNEL, &recv) != RK_SUCCESS) {
        RK_MPI_VENC_DestroyChn(CHANNEL);
        return -1;
    }
    return 0;
}

static int start_capture(struct capture *c, unsigned w, unsigned h, double fps, int kbps) {
    c->width = w, c->height = h;
    if ((c->video = open(VIDEO_DEV, O_RDWR | O_NONBLOCK)) < 0) return -1;
    struct v4l2_format fmt = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE};
    fmt.fmt.pix_mp.width = w, fmt.fmt.pix_mp.height = h;
    fmt.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_YUYV;
    fmt.fmt.pix_mp.field = V4L2_FIELD_NONE;
    fmt.fmt.pix_mp.num_planes = 1;
    if (xioctl(c->video, VIDIOC_S_FMT, &fmt) < 0) return -1;
    size_t size = fmt.fmt.pix_mp.plane_fmt[0].sizeimage ? fmt.fmt.pix_mp.plane_fmt[0].sizeimage : (size_t)w * h * 2;

    MB_POOL_CONFIG_S pool = {.u64MBSize = size, .u32MBCnt = BUFFERS, .enRemapMode = MB_REMAP_MODE_NOCACHE,
                             .enAllocType = MB_ALLOC_TYPE_DMA, .enDmaType = MB_DMA_TYPE_CMA, .bPreAlloc = RK_TRUE};
    if ((c->pool = RK_MPI_MB_CreatePool(&pool)) == MB_INVALID_POOLID) return -1;
    struct v4l2_requestbuffers req = {.count = BUFFERS, .type = fmt.type, .memory = V4L2_MEMORY_DMABUF};
    if (xioctl(c->video, VIDIOC_REQBUFS, &req) < 0 || req.count < BUFFERS) return -1;
    for (int i = 0; i < BUFFERS; i++) {
        if (!(c->blk[i] = RK_MPI_MB_GetMB(c->pool, size, RK_TRUE)) || (c->fd[i] = RK_MPI_MB_Handle2Fd(c->blk[i])) < 0)
            return -1;
        struct v4l2_plane plane = {.m.fd = c->fd[i], .length = (unsigned)size};
        struct v4l2_buffer buf = {.index = (unsigned)i, .type = fmt.type, .memory = V4L2_MEMORY_DMABUF,
                                  .m.planes = &plane, .length = 1};
        if (xioctl(c->video, VIDIOC_QBUF, &buf) < 0) return -1;
    }
    if (start_encoder(w, h, fps, kbps) < 0) return -1;
    c->encoding = 1;
    enum v4l2_buf_type type = fmt.type;
    return xioctl(c->video, VIDIOC_STREAMON, &type);
}

/* encode_one takes a captured frame, encodes it and writes it out, and gives the buffer back.
 * 1: a frame went out; 0: none yet; -1: the capture failed; -2: the spoke stopped reading. */
static int encode_one(struct capture *c) {
    struct v4l2_plane plane = {0};
    struct v4l2_buffer buf = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .memory = V4L2_MEMORY_DMABUF,
                              .m.planes = &plane, .length = 1};
    if (xioctl(c->video, VIDIOC_DQBUF, &buf) < 0) return errno == EAGAIN ? 0 : -1;
    int result = 1;
    VIDEO_FRAME_INFO_S frame;
    memset(&frame, 0, sizeof frame);
    frame.stVFrame.pMbBlk = c->blk[buf.index];
    frame.stVFrame.u32Width = c->width, frame.stVFrame.u32Height = c->height;
    frame.stVFrame.u32VirWidth = (c->width + 1) & ~1u, frame.stVFrame.u32VirHeight = (c->height + 1) & ~1u;
    frame.stVFrame.enPixelFormat = RK_FMT_YUV422_YUYV;
    frame.stVFrame.u64PTS = (RK_U64)buf.timestamp.tv_sec * 1000000 + buf.timestamp.tv_usec;
    if (RK_MPI_VENC_SendFrame(CHANNEL, &frame, 1000) == RK_SUCCESS) {
        VENC_PACK_S pack;
        VENC_STREAM_S stream = {.pstPack = &pack, .u32PackCount = 1};
        if (RK_MPI_VENC_GetStream(CHANNEL, &stream, 1000) == RK_SUCCESS) {
            const uint8_t *data = RK_MPI_MB_Handle2VirAddr(pack.pMbBlk);
            if (data && write_frame(data, pack.u32Len) < 0) result = -2;
            RK_MPI_VENC_ReleaseStream(CHANNEL, &stream);
        } else {
            usleep(10000); /* not a busy loop on the one core, if the encoder's behind */
        }
    }
    if (xioctl(c->video, VIDIOC_QBUF, &buf) < 0) return -1; /* only once the encoder's done with it */
    return result;
}

/* ---- The loop: wait for a signal, capture it until it changes, again ---- */

int main(int argc, char **argv) {
    int fps = 30, kbps = 8000;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "-fps") == 0) fps = atoi(argv[i + 1]);
        else if (strcmp(argv[i], "-bitrate") == 0) kbps = atoi(argv[i + 1]);
    }
    if (kbps < 500 || kbps > 20000) kbps = 8000;
    signal(SIGPIPE, SIG_IGN); /* a write fails instead, and it stops */

    int subdev = open(SUBDEV, O_RDWR);
    if (subdev < 0) {
        fprintf(stderr, "Can't open the HDMI input (%s): %s\n", SUBDEV, strerror(errno));
        return 1;
    }
    set_edid(subdev, fps);
    struct v4l2_event_subscription sub = {.type = V4L2_EVENT_SOURCE_CHANGE};
    if (xioctl(subdev, VIDIOC_SUBSCRIBE_EVENT, &sub) < 0)
        fprintf(stderr, "No source-change events (%s): checking every second instead\n", strerror(errno));
    if (RK_MPI_SYS_Init() != RK_SUCCESS) {
        fprintf(stderr, "Rockchip's MPI didn't start\n");
        return 1;
    }

    struct capture c = {.video = -1, .pool = MB_INVALID_POOLID};
    int said = -1; /* what was said last: -1 nothing, 0 no signal, 1 a signal */
    for (;;) {
        struct v4l2_dv_timings dv;
        double rate;
        if (!locked(subdev, &dv, &rate)) {
            if (said != 0) say_signal(0, 0, 0, 0), said = 0;
            struct pollfd p = {.fd = subdev, .events = POLLPRI};
            if (poll(&p, 1, 1000) > 0) {
                struct v4l2_event ev;
                xioctl(subdev, VIDIOC_DQEVENT, &ev);
            }
            continue;
        }
        if (start_capture(&c, dv.bt.width, dv.bt.height, rate, kbps) < 0) {
            fprintf(stderr, "Couldn't start capturing %ux%u: %s\n", dv.bt.width, dv.bt.height, strerror(errno));
            stop_capture(&c);
            sleep(1);
            continue;
        }
        say_signal(1, dv.bt.width, dv.bt.height, rate), said = 1;
        time_t last = time(NULL);
        for (;;) {
            struct pollfd p[2] = {{.fd = c.video, .events = POLLIN}, {.fd = subdev, .events = POLLPRI}};
            int n = poll(p, 2, 1000);
            if (n > 0 && p[1].revents & POLLPRI) { /* the source changed: start over */
                struct v4l2_event ev;
                xioctl(subdev, VIDIOC_DQEVENT, &ev);
                break;
            }
            if (n > 0 && p[0].revents & POLLIN) {
                int r = encode_one(&c);
                if (r == -2) return 0; /* the spoke has gone */
                if (r < 0) break;
                if (r > 0) last = time(NULL);
            }
            if (time(NULL) - last > 2) break; /* no frames for 2 s: the signal's gone */
        }
        stop_capture(&c);
        said = -1; /* say what's coming in next, whatever it is */
    }
}
#endif
