# Use a lightweight python slim image instead of the massive official playwright image
FROM python:3.11-slim

# Set environment variables early for consistent build layer metadata
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:99 \
    RESOLUTION=1280x800x24 \
    HOST=0.0.0.0 \
    PORT=8000

# Install VNC, Xvfb, fluxbox, and novnc for headless browser login
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    fluxbox \
    novnc \
    net-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the layer caches across code changes.
COPY requirements.txt .
# Install pip dependencies, then use playwright to install ONLY chromium and its specific OS dependencies.
# Clean apt caches afterwards to significantly reduce image size.
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache

# Copy application source code
COPY . .

# Ensure entrypoint is executable
RUN chmod +x /app/entrypoint.sh

# Expose FastAPI port
EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]

