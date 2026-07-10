# Use a lightweight python slim image instead of the massive official playwright image
FROM python:3.11-slim

# Install VNC, Xvfb, fluxbox, and novnc for headless browser login
RUN apt-get update && apt-get install -y \
    xvfb \
    x11vnc \
    fluxbox \
    novnc \
    net-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Ensure Playwright installs to a deterministic path so our browser.py can find it or not override it.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install Python deps first so the layer caches across code changes.
COPY requirements.txt .
# Install pip dependencies, then use playwright to install ONLY chromium and its specific OS dependencies
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

# Set up VNC and Display
ENV DISPLAY=:99
ENV RESOLUTION=1280x800x24
ENV HOST=0.0.0.0
ENV PORT=8000
ENV PYTHONUNBUFFERED=1

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# Expose FastAPI port
EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
