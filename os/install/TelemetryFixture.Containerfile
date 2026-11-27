FROM docker.io/library/debian:trixie-slim@sha256:1e1a22175fbf1b32157ddaf0219e1db4e5401fab2b94ed285add64d18587cd2d
RUN apt-get update && apt-get install -y --no-install-recommends mtd-utils=1:2.3.0-1 \
    && rm -rf /var/lib/apt/lists/*
