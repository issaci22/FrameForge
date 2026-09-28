#!/bin/sh
# Generate small synthetic recordings for trying FrameForge end to end.
# Some files are backdated so age-based rules have something to match.
#
# It needs FFmpeg, so run it in the local development image (frameforge:dev), from the repository root.
# Inside the running dev container (writes to ./dev-media):
#   docker compose -f docker-compose.dev.yml exec -T -u frameforge dev sh -s /media/test-vods < scripts/make-test-media.sh
# or with a throwaway container (build the image first: docker compose -f docker-compose.dev.yml build dev):
#   docker run --rm --user 1000:1000 --entrypoint sh -v "$PWD/dev-media:/media" -v "$PWD/scripts:/scripts:ro" frameforge:dev /scripts/make-test-media.sh /media/test-vods
set -eu

OUT="${1:-/media/test-vods}"
DUR="${DUR:-12}"
mkdir -p "$OUT/2024" "$OUT/shorts"

ff() { ffmpeg -hide_banner -loglevel error -y "$@"; }
age() { touch -d "@$(( $(date +%s) - $2 * 86400 ))" "$1"; }  # age <file> <days>

echo "Writing test media to $OUT (duration ${DUR}s each)…"

# 1080p60 H.264 stream VOD with two audio tracks + a subtitle, 400 days old
printf '1\n00:00:01,000 --> 00:00:04,000\nHello chat\n' > /tmp/ff-sub.srt
ff -f lavfi -i "testsrc2=s=1920x1080:r=60:d=$DUR" -f lavfi -i "sine=f=440:d=$DUR" -f lavfi -i "sine=f=660:d=$DUR" -i /tmp/ff-sub.srt \
   -map 0 -map 1 -map 2 -map 3 -c:v libx264 -preset ultrafast -crf 18 -c:a aac -b:a 160k -c:s srt \
   -metadata:s:a:0 title="Game" -metadata:s:a:1 title="Mic" "$OUT/2024/stream-2024-08-01.mkv"
age "$OUT/2024/stream-2024-08-01.mkv" 400

# 1440p H.264 gameplay, 120 days old
ff -f lavfi -i "testsrc2=s=2560x1440:r=60:d=$DUR" -f lavfi -i "sine=f=300:d=$DUR" \
   -c:v libx264 -preset ultrafast -crf 16 -c:a aac "$OUT/gameplay-1440p.mp4"
age "$OUT/gameplay-1440p.mp4" 120

# Recent 1080p recording, 5 days old (should be left alone by aging rules)
ff -f lavfi -i "testsrc2=s=1920x1080:r=30:d=$DUR" -f lavfi -i "sine=f=500:d=$DUR" \
   -c:v libx264 -preset ultrafast -crf 20 -c:a aac "$OUT/recent-recording.mkv"
age "$OUT/recent-recording.mkv" 5

# Vertical short (1080x1920), 100 days old
ff -f lavfi -i "testsrc2=s=1080x1920:r=30:d=$DUR" -f lavfi -i "sine=f=800:d=$DUR" \
   -c:v libx264 -preset ultrafast -crf 20 -c:a aac "$OUT/shorts/vertical-short.mp4"
age "$OUT/shorts/vertical-short.mp4" 100

# Already HEVC, 200 days old (rules should skip: nothing to gain)
ff -f lavfi -i "testsrc2=s=1920x1080:r=30:d=$DUR" -f lavfi -i "sine=f=350:d=$DUR" \
   -c:v libx265 -preset ultrafast -x265-params log-level=error -c:a aac "$OUT/already-hevc.mkv"
age "$OUT/already-hevc.mkv" 200

# Interrupted OBS recording: truncated file, 150 days old (should fail safely)
ff -f lavfi -i "testsrc2=s=1920x1080:r=30:d=$DUR" -c:v libx264 -preset ultrafast "$OUT/.partial.mp4"
head -c 200000 "$OUT/.partial.mp4" > "$OUT/crashed-obs-recording.mp4"
rm -f "$OUT/.partial.mp4"
age "$OUT/crashed-obs-recording.mp4" 150

echo "Done:"
ls -la "$OUT" "$OUT/2024" "$OUT/shorts"
