# The emulator on the PicoKVM's own system: Luckfox's root filesystem (uClibc, BusyBox, ffmpeg 4.4),
# from the system partition of their microSD image, so the spoke runs on what the box has.
# tools/dev builds it when PICOKVM_SYSTEM names that partition (the repo's tools/dev firmware takes it out).
FROM --platform=$BUILDPLATFORM alpine:3.22 AS unpack
ARG SYSTEM=system.img
RUN apk add --no-cache e2fsprogs-extra
COPY ${SYSTEM} /system.img
RUN mkdir /rootfs && debugfs -R "rdump / /rootfs" /system.img >/dev/null 2>&1 \
    && test -x /rootfs/usr/bin/ffmpeg && chmod 1777 /rootfs/tmp

FROM scratch
COPY --from=unpack /rootfs /
