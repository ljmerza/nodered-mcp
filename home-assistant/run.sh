#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# Map the add-on options onto nodered-mcp's environment variables.

export NODERED_URL
NODERED_URL="$(bashio::config 'nodered_url')"
NODERED_URL="${NODERED_URL%/}"

if bashio::config.has_value 'username'; then
    export NODERED_USERNAME NODERED_PASSWORD
    NODERED_USERNAME="$(bashio::config 'username')"
    NODERED_PASSWORD="$(bashio::config 'password')"
fi
if bashio::config.has_value 'nodered_token'; then
    export NODERED_TOKEN
    NODERED_TOKEN="$(bashio::config 'nodered_token')"
fi
if bashio::config.false 'verify_ssl'; then
    export NODERED_INSECURE=true
fi
if bashio::config.true 'read_only'; then
    export NODERED_READ_ONLY=true
fi
if bashio::config.has_value 'auth_token'; then
    export NODERED_MCP_AUTH_TOKEN
    NODERED_MCP_AUTH_TOKEN="$(bashio::config 'auth_token')"
fi
if bashio::config.has_value 'group_style'; then
    export NODERED_GROUP_STYLE
    NODERED_GROUP_STYLE="$(bashio::config 'group_style')"
fi

export NODERED_MCP_TRANSPORT=http NODERED_MCP_HOST=0.0.0.0 NODERED_MCP_PORT=8080

# Both add-ons start at boot, and nodered-mcp exits if it cannot read the
# flows. Wait until Node-RED answers at all; curl without -f succeeds on any
# HTTP status, including the 401 the Node-RED add-on's proxy returns.
until curl -sk -o /dev/null --max-time 5 "${NODERED_URL}/"; do
    bashio::log.info "Waiting for Node-RED at ${NODERED_URL}..."
    sleep 5
done

bashio::log.info "Starting nodered-mcp"
exec nodered-mcp
