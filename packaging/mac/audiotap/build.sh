#!/bin/sh
# Build earshot-audiotap (the system-audio capture helper) for Apple Silicon.
# Output: packaging/mac/bin/earshot-audiotap
set -e
cd "$(dirname "$0")"
mkdir -p ../bin
xcrun swiftc -O \
    -swift-version 5 \
    -target arm64-apple-macos14.4 \
    main.swift \
    -o ../bin/earshot-audiotap \
    -framework CoreAudio \
    -framework AVFAudio \
    -framework Foundation
echo "built $(cd ../bin && pwd)/earshot-audiotap"
