ARG BUILD_FROM
FROM ${BUILD_FROM}

# The add-on version in config.yaml is the nodered-mcp version it installs.
ARG BUILD_VERSION
RUN pip install --no-cache-dir "nodered-mcp==${BUILD_VERSION}"

COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
