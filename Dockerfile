# Use a multi-stage build optimized for change frequency
FROM python:3.12-slim AS python-base

# Set up environment variables early
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONOPTIMIZE=2 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PUID="911" \
    PGID="911" \
    VERBOSE=false \
    DEBIAN_FRONTEND=noninteractive

# Stage 1: Install runtime system dependencies (rarely change)
FROM python-base AS system-deps

# Install runtime dependencies in a single layer with cache mount
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    jq \
    libssl3 \
    openssl \
    poppler-utils \
    python3-xdg \
    uuid-runtime \
    xz-utils && \
    apt-get clean && \
    rm -rf /tmp/* /var/tmp/*

# Stage 2: Create user (rarely changes)
FROM system-deps AS user-setup

# Create user and basic setup
RUN groupadd --gid "$PGID" abc && \
    useradd --create-home --shell /bin/bash --uid "$PUID" --gid abc abc

# Stage 3: Install stable Python dependencies (rarely change)
FROM user-setup AS python-stable-deps

# Install stable Python packages from requirements.txt
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    echo "*** Install Stable Python Packages ***" && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm -f /tmp/requirements.txt

# Stage 4: Calibre installation (changes monthly)
FROM python-stable-deps AS calibre-installer

# BuildKit automatic platform detection
ARG TARGETPLATFORM
ARG BUILDPLATFORM
ARG TARGETOS
ARG TARGETARCH

# Set version labels
ARG VERSION
ARG CALIBRE_RELEASE
LABEL build_version="FFDL-Auto version:- ${VERSION} Calibre: ${CALIBRE_RELEASE}"

# Download and extract Calibre (optimized multi-architecture)
RUN --mount=type=cache,target=/tmp/calibre-cache \
    --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    echo "**** install calibre ****" && \
    # Use BuildKit's automatic platform detection instead of uname
    case "${TARGETPLATFORM:-linux/amd64}" in \
    "linux/amd64") \
    echo "Installing Calibre from official binaries for amd64" && \
    if [ -z "${CALIBRE_RELEASE}" ]; then \
    CALIBRE_RELEASE_TAG=$(curl -sX GET "https://api.github.com/repos/kovidgoyal/calibre/releases/latest" | jq -r .tag_name); \
    CALIBRE_VERSION=$(echo "${CALIBRE_RELEASE_TAG}" | sed 's/^v//'); \
    else \
    CALIBRE_VERSION=$(echo "${CALIBRE_RELEASE}" | sed 's/^v//'); \
    fi && \
    echo "Using Calibre version: ${CALIBRE_VERSION}" && \
    CALIBRE_URL="https://download.calibre-ebook.com/${CALIBRE_VERSION}/calibre-${CALIBRE_VERSION}-x86_64.txz" && \
    CALIBRE_CACHE_FILE="/tmp/calibre-cache/calibre-${CALIBRE_VERSION}-x86_64.txz" && \
    echo "Downloading from ${CALIBRE_URL}" && \
    # Check if cached version exists and is valid
    if [ ! -f "${CALIBRE_CACHE_FILE}" ] || [ ! -s "${CALIBRE_CACHE_FILE}" ]; then \
    echo "Downloading fresh copy to cache..." && \
    curl -o "${CALIBRE_CACHE_FILE}" -L "${CALIBRE_URL}" && \
    echo "Download complete, extracting..." ; \
    else \
    echo "Using cached Calibre binary..." ; \
    fi && \
    mkdir -p /opt/calibre && \
    tar xf "${CALIBRE_CACHE_FILE}" -C /opt/calibre && \
    # Set up official Calibre binaries
    if [ -f "/opt/calibre/calibre_postinstall" ]; then \
    chmod +x /opt/calibre/calibre_postinstall && \
    echo "*** Running Calibre post-install ***" && \
    (/opt/calibre/calibre_postinstall || echo "Post-install failed, continuing without it") ; \
    fi && \
    echo "*** Setting up Calibre symlinks (calibredb only) ***" && \
    find /opt/calibre -name "calibredb" -type f -executable -exec ln -sf {} /usr/local/bin/calibredb \; && \
    # Configure library path for Calibre shared libraries
    echo "/opt/calibre/lib" >> /etc/ld.so.conf.d/calibre.conf && \
    echo "/opt/calibre" >> /etc/ld.so.conf.d/calibre.conf && \
    find /opt/calibre -name "*.so*" -type f -exec dirname {} \; | sort -u | while read libdir; do \
        echo "$libdir" >> /etc/ld.so.conf.d/calibre.conf; \
    done && \
    ldconfig && \
    echo "*** Calibre library configuration complete ***" && \
    echo "*** Minimal Calibre cleanup - preserving calibredb dependencies ***" && \
    # Only remove the most obvious GUI applications, keep everything else for safety
    rm -f /opt/calibre/calibre /opt/calibre/ebook-viewer /opt/calibre/ebook-edit 2>/dev/null || true && \
    rm -f /opt/calibre/calibre-server /opt/calibre/calibre-smtp 2>/dev/null || true && \
    # Remove only GUI Python packages that are clearly not needed
    rm -rf /opt/calibre/lib/python*/site-packages/calibre/gui2 2>/dev/null || true && \
    rm -rf /opt/calibre/lib/python*/site-packages/calibre/srv 2>/dev/null || true && \
    # Remove only viewer/editor resources, keep everything else
    rm -rf /opt/calibre/resources/viewer 2>/dev/null || true && \
    rm -rf /opt/calibre/resources/editor 2>/dev/null || true && \
    # Verify calibredb can run after cleanup
    echo "*** Testing calibredb functionality ***" && \
    /opt/calibre/calibredb --version && \
    echo "*** Calibredb test successful ***" && \
    # Show what libraries are available
    echo "*** Available Calibre libraries ***" && \
    find /opt/calibre -name "*.so*" -type f 2>/dev/null | head -20 && \
    echo "*** Minimal Calibre cleanup complete ***" \
    ;; \
    "linux/arm64"|"linux/arm/v7"|"linux/arm/v6") \
    echo "Installing Calibre from system packages for ARM architecture (calibredb only)" && \
    apt-get update && \
    apt-get install -y --no-install-recommends calibre && \
    # Create consistent symlink for ARM (calibredb only)
    ln -sf /usr/bin/calibredb /usr/local/bin/calibredb && \
    echo "*** Removing unnecessary Calibre components from system installation ***" && \
    # Remove GUI applications and conversion tools (keep calibredb and essential dependencies)
    rm -f /usr/bin/calibre /usr/bin/ebook-convert /usr/bin/ebook-edit /usr/bin/ebook-polish 2>/dev/null || true && \
    rm -f /usr/bin/ebook-viewer /usr/bin/lrf* /usr/bin/web2disk 2>/dev/null || true && \
    # Don't remove calibre-bin package as it may contain essential libraries for calibredb
    echo "*** ARM Calibre cleanup complete ***" \
    ;; \
    *) \
    echo "Unsupported platform: ${TARGETPLATFORM}" && \
    echo "Attempting system package installation as fallback..." && \
    apt-get update && \
    apt-get install -y --no-install-recommends calibre && \
    ln -sf /usr/bin/calibredb /usr/local/bin/calibredb && \
    echo "*** Removing unnecessary Calibre components from fallback installation ***" && \
    # Remove GUI applications and conversion tools (keep calibredb and essential dependencies)
    rm -f /usr/bin/calibre /usr/bin/ebook-convert /usr/bin/ebook-edit /usr/bin/ebook-polish 2>/dev/null || true && \
    rm -f /usr/bin/ebook-viewer /usr/bin/lrf* /usr/bin/web2disk 2>/dev/null || true && \
    echo "*** Fallback Calibre cleanup complete ***" \
    ;; \
    esac && \
    echo "*** Calibre setup complete ***"

# Stage 5: FanFicFare installation (changes weekly)
FROM calibre-installer AS fanficfare-installer

ARG FANFICFARE_VERSION
RUN --mount=type=cache,target=/root/.cache/pip \
    set -e && \
    echo "*** Checking SSL support ***" && \
    python3 -c "import ssl; print('SSL support available')" && \
    echo "*** Install FanFicFare ***" && \
    # Try TestPyPI first, then fallback to regular PyPI
    if [ -n "${FANFICFARE_VERSION}" ]; then \
        PACKAGE_SPEC="FanFicFare==${FANFICFARE_VERSION}" && \
        echo "Installing FanFicFare==${FANFICFARE_VERSION}" ; \
    else \
        PACKAGE_SPEC="FanFicFare" && \
        echo "Installing latest FanFicFare" ; \
    fi && \
    # Try TestPyPI first
    echo "*** Attempting TestPyPI installation ***" && \
    if pip install --no-cache-dir -i "https://test.pypi.org/simple/" --extra-index-url "https://pypi.org/simple/" "${PACKAGE_SPEC}"; then \
        echo "*** FanFicFare installed from TestPyPI ***" ; \
    else \
        echo "*** TestPyPI failed, trying regular PyPI ***" && \
        pip install --no-cache-dir "${PACKAGE_SPEC}" && \
        echo "*** FanFicFare installed from PyPI ***" ; \
    fi && \
    echo "*** FanFicFare installation complete ***"

# Stage 6: Application code (changes with every code update)
FROM fanficfare-installer AS app-code

# Copy application files
COPY root/ /
RUN chmod -R +x /app/ && \
    chown -R abc:abc /app/

# Final runtime stage - minimal, just sets labels and runtime configuration
FROM app-code AS runtime

# Set version labels
ARG VERSION
ARG CALIBRE_RELEASE
LABEL build_version="FFDL-Auto version:- ${VERSION} Calibre: ${CALIBRE_RELEASE}"

# Runtime configuration
ENV VERBOSE=false

VOLUME /config
WORKDIR /config

CMD ["/app/entrypoint.sh"]