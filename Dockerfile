FROM python:3.11

ARG CHROME_VERSION="138.0.7204.94"
ARG CHROME_LINK=https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64

RUN apt-get update && apt-get install -y \
    curl unzip \
    libnspr4 libnss3 libxss1 libasound2 libatk1.0-0 \
    libatk-bridge2.0-0 libcups2 libdrm2 libxcomposite1 \
    libxrandr2 libgbm1 libxdamage1 libgtk-3-0 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Download and install ChromeDriver
RUN curl -sSL ${CHROME_LINK}/chromedriver-linux64.zip -o /tmp/chromedriver.zip \
    && unzip -q /tmp/chromedriver.zip -d /tmp \
    && mv /tmp/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver \
    && chmod +x /usr/local/bin/chromedriver \
    && rm -rf /tmp/chromedriver.zip /tmp/chromedriver-linux64

# Download and install Chrome
RUN curl -sSL ${CHROME_LINK}/chrome-linux64.zip -o /tmp/chrome.zip \
    && unzip -q /tmp/chrome.zip -d /tmp/chrome-linux64 \
    && mkdir -p /opt/google \
    && mv /tmp/chrome-linux64/chrome-linux64 /opt/google/chrome \
    && ln -sf /opt/google/chrome/chrome /usr/bin/google-chrome \
    && rm -rf /tmp/chrome.zip /tmp/chrome-linux64

RUN google-chrome --version
RUN chromedriver --version

WORKDIR /pennyspy
RUN pip install git+https://github.com/moqba/PennySpy

ENTRYPOINT ["pennyspy_api"]
