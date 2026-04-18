@echo off
cd /d %~dp0
python -m http.server 8080 --bind 127.0.0.1
start "" "http://127.0.0.1:8080"