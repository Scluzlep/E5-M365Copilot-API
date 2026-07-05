#!/bin/bash
set -e

# Start X virtual framebuffer
Xvfb $DISPLAY -screen 0 ${RESOLUTION} -ac +extension RANDR &
sleep 1

# Start window manager (helps with dialogs and window focus in VNC)
fluxbox &

# Start x11vnc without password (since it will be accessed via localhost/noVNC inside container or safely proxied)
x11vnc -display $DISPLAY -nopw -listen localhost -xkb -ncache 10 -ncache_cr -forever &


# Start the FastAPI server
echo "Starting E5 M365 Copilot API Server on port 8000..."
python -m server
