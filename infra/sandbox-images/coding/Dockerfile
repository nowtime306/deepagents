FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
RUN apt-get update && apt-get install -y --no-install-recommends git nodejs npm openjdk-21-jdk-headless maven ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
