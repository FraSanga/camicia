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

ARG PROJECTS_USER
RUN useradd -m -s /bin/bash ${PROJECTS_USER} && echo "${PROJECTS_USER} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

WORKDIR /usr/local/src
RUN git clone --depth 1 https://github.com/BOINC/boinc.git
WORKDIR /usr/local/src/boinc
RUN ./_autosetup && ./configure --disable-client --disable-manager && make
COPY camicia.httpd.conf /etc/apache2/sites-available/boinc.conf
RUN a2dissite 000-default && a2ensite boinc

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ARG SERVER_VOLUME_PROJECTS_DIR
WORKDIR ${SERVER_VOLUME_PROJECTS_DIR}
EXPOSE 80

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]