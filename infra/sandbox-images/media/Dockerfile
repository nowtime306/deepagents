FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 media && useradd --uid 10001 --gid 10001 --no-create-home media
USER 10001:10001
WORKDIR /media
