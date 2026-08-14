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
    && rm -rf /var/lib/apt/lists/*

RUN a2enmod cgi rewrite headers expires

# Reduce version-fingerprinting surface: don't advertise the Apache/PHP
# version in the Server header or on default error pages.
RUN sed -i \
    -e 's/^ServerTokens .*/ServerTokens Prod/' \
    -e 's/^ServerSignature .*/ServerSignature Off/' \
    /etc/apache2/conf-available/security.conf

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
ARG BOINC_COMMIT=d498cd24c27462786977947f9d0315663fe46be9
WORKDIR /usr/local/src
RUN git init boinc && \
    cd boinc && \
    git remote add origin https://github.com/BOINC/boinc.git && \
    git fetch --depth 1 origin ${BOINC_COMMIT} && \
    git checkout FETCH_HEAD
WORKDIR /usr/local/src/boinc
RUN ./_autosetup && ./configure --disable-client --disable-manager && make
COPY camicia.httpd.conf /etc/apache2/sites-available/boinc.conf
RUN a2dissite 000-default && a2ensite boinc

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY healthcheck.sh /usr/local/bin/healthcheck.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh

ARG SERVER_VOLUME_PROJECTS_DIR
WORKDIR ${SERVER_VOLUME_PROJECTS_DIR}
EXPOSE 80

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]