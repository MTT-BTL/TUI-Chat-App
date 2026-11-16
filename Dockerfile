FROM debian:trixie-slim

WORKDIR /app

ENV APP_DATA_DIR=./server/.runtime

COPY build/server-app ./server-app

RUN chmod 0755 ./server-app \
    && mkdir -p ./server/.runtime ./server/download

VOLUME ["/app/server/.runtime", "/app/server/download"]

EXPOSE 8443/tcp

ENTRYPOINT ["./server-app"]
CMD ["--host", "0.0.0.0", "--download-dir", "./server/download", "server"]