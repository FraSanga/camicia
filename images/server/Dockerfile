FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    # --- Server Web & PHP ---
    apache2 \
    libapache2-mod-php \
    apache2-utils \
    php \
    php-mysql \
    php-xml \
    php-cli \
    php-gd \
    php-curl \
    # --- Database Client ---
    mariadb-client \
    libmariadb-dev-compat \
    # --- Tools ---
    git \
    build-essential \
    pkg-config \
    autoconf \
    automake \
    libtool \
    make \
    # --- Windows cross-compile toolchain (worker app only, see
    # boinc-win build below) ---
    mingw-w64 \
    # --- Boinc required libraries ---
    libssl-dev \
    libcurl4-openssl-dev \
    nlohmann-json3-dev \
    libzip-dev \
    # --- Python & Scripting ---
    python3 \
    python3-pip \
    python3-mysqldb \
    libpython3-dev \
    python-is-python3 \
    # --- System tools ---
    curl \
    wget \
    ca-certificates \
    cron \
    unzip \
    rsync \
    && rm -rf /var/lib/apt/lists/*

RUN a2enmod cgi rewrite headers expires

# Reduce version-fingerprinting surface: don't advertise the Apache/PHP
# version in the Server header or on default error pages.
RUN sed -i \
    -e 's/^ServerTokens .*/ServerTokens Prod/' \
    -e 's/^ServerSignature .*/ServerSignature Off/' \
    /etc/apache2/conf-available/security.conf

# rclone: offsite backup to Google Drive (tools/backup_offsite_gdrive.sh).
# Official install script rather than Debian bookworm's apt package, which
# lags upstream releases significantly.
RUN curl -fsSL https://rclone.org/install.sh | bash

ARG PROJECTS_USER
# No sudo grant: nothing in the deploy/compile flow (tools.sh) or entrypoint.sh
# ever invokes sudo as this user. It runs the compiled assimilator/worker/
# work_generator binaries, which process untrusted client-submitted result
# files, so passwordless root here would be a meaningfully larger blast radius
# than this user actually needs.
RUN useradd -m -s /bin/bash ${PROJECTS_USER}

# Pinned rather than a floating `master` clone, so every image rebuild compiles
# the exact same, already-verified-building BOINC source instead of silently
# picking up whatever upstream commit happens to be current (a reproducibility
# and supply-chain-hygiene gap otherwise, since this gets compiled with root
# build privileges automatically on every rebuild). Bump deliberately when you
# want a newer BOINC.
ARG BOINC_COMMIT=9969ba684cad0dc10d19b5ada0814f44401f8a68
WORKDIR /usr/local/src
RUN git init boinc && \
    cd boinc && \
    git remote add origin https://github.com/BOINC/boinc.git && \
    git fetch --depth 1 origin ${BOINC_COMMIT} && \
    git checkout FETCH_HEAD
WORKDIR /usr/local/src/boinc
RUN ./_autosetup && ./configure --disable-client --disable-manager && make

# Second, fully independent copy of the same pinned BOINC source, built with
# the mingw-w64 cross-compiler so the worker app can also ship for
# windows_x86_64 (the largest platform by BOINC volunteer count, and the one
# this project was missing entirely -- see tools.sh's "Compiling Worker
# (Windows)" step for what actually links against this). A second clone
# rather than an out-of-tree/VPATH build of the same tree: BOINC's autotools
# setup isn't confirmed to support VPATH cleanly, and this keeps the Windows
# build fully isolated from the native one above -- it cannot break the
# existing, working Linux build regardless of anything that goes wrong here.
# Only lib/ and api/ are needed (worker.cpp links libboinc.a + libboinc_api.a,
# not libboinc_crypt.a), so client/manager/server all stay disabled here too.
WORKDIR /usr/local/src
RUN git init boinc-win && \
    cd boinc-win && \
    git remote add origin https://github.com/BOINC/boinc.git && \
    git fetch --depth 1 origin ${BOINC_COMMIT} && \
    git checkout FETCH_HEAD
WORKDIR /usr/local/src/boinc-win
RUN ./_autosetup && \
    ./configure --host=x86_64-w64-mingw32 --disable-client --disable-manager --disable-server && \
    make

WORKDIR /usr/local/src/boinc
# Kept as a separate, never-mutated template: entrypoint.sh regenerates
# sites-available/boinc.conf from this on every container start, not just
# once. sed -i'ing the live file directly (the previous approach) consumes
# the ${DOMAIN}/${SERVER_VOLUME_PROJECTS_DIR} placeholders on the first
# substitution -- since that file lives in the container's writable layer
# and survives a plain restart, a later .env change would silently never
# take effect until the container was recreated from the image.
COPY camicia.httpd.conf /etc/apache2/sites-available/boinc.conf.template
RUN cp /etc/apache2/sites-available/boinc.conf.template /etc/apache2/sites-available/boinc.conf && \
    a2dissite 000-default && a2ensite boinc

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY healthcheck.sh /usr/local/bin/healthcheck.sh
COPY fix_permissions.sh /usr/local/bin/fix_permissions.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh /usr/local/bin/fix_permissions.sh

ARG SERVER_VOLUME_PROJECTS_DIR
WORKDIR ${SERVER_VOLUME_PROJECTS_DIR}
EXPOSE 80

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]