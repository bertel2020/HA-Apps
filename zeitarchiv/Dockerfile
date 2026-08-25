FROM python:3.12-slim

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y nginx \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY VERSION ./VERSION
COPY app ./app
COPY nginx.conf /etc/nginx/conf.d/zeitarchiv.conf
COPY run.sh /usr/local/bin/zeitarchiv-run
RUN chmod 0755 /usr/local/bin/zeitarchiv-run \
    && nginx -t

EXPOSE 8099 8127

CMD ["/usr/local/bin/zeitarchiv-run"]
