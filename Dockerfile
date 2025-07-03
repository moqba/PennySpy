FROM python:3.11-slim
LABEL authors="moqba"

RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    git \
&& apt-get clean && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV PATH="$PATH:/usr/bin"

WORKDIR /app

RUN pip install git+https://github.com/moqba/PennySpy.git@fastapi_integration
ENTRYPOINT ["pennyspy_api"]