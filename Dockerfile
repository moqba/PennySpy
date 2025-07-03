FROM python:3.11-slim
LABEL authors="moqba"

RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV PATH="$PATH:/usr/bin"

WORKDIR /app
RUN pip install .
CMD ["./bin/docker_start"]
