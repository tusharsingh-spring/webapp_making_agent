#!/bin/bash
cd "$(dirname "$0")"
python -m http.server 8080 --bind 127.0.0.1
open "http://127.0.0.1:8080" 2>/dev/null || xdg-open "http://127.0.0.1:8080" 2>/dev/null || true