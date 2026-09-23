#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "${RUNNER_OS:-$(uname -s)}" == Windows || "$OSTYPE" == msys* ]]; then
    export PATH="$(dirname "$(command -v cl)"):/c/Strawberry/perl/bin:$PATH"
    python tools/build_release.py windows
else
    sudo apt-get update -qq
    sudo apt-get install -y build-essential libpcre2-dev zlib1g-dev libssl-dev python3 curl
    python3 tools/build_release.py linux
fi
