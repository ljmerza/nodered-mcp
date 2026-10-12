"""Writing flows.json back out, and the layout gate that guards it."""

import base64
import json
import os
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request

from . import layout
from .constants import ADMIN_TIMEOUT, DeployError, LayoutError, StaleFlowsError


class PersistMixin:
    """Commit in-memory edits to disk, or to disk and the running Node-RED."""

    def save(self, allow_overlap=False, quiet=False):
        """Write flows.json back to disk.

        Before writing, lints the canvas layout and refuses if this edit
        introduces a NEW error-level problem -- a group landing on top of
        another group, or a group box that no longer covers its own nodes.
        Problems that already existed on disk never block. Warnings
        (node-on-node collisions, nodes inside a foreign group box) print.

        Args:
            allow_overlap: Write anyway, printing what would have blocked.
            quiet: Suppress the "no new layout problems" line.

        Raises:
            LayoutError: A new error-level layout problem was introduced.
            PermissionError: flows.json is not writable by this user.

        Written in place rather than to a temp file and renamed: a `-v
        /host/flows.json:/data/flows.json` bind mount is the file's inode, so
        an atomic replace would swap it out from under the container and the
        running Node-RED would never see the new content.
        """
        if not self.path:
            raise DeployError(
                "there is no local flows.json to save to -- this session reads the "
                f"flows from {self.admin_url} over the Admin API. Use deploy to push "
                "the edits back to Node-RED."
            )
        self._check_layout(allow_overlap=allow_overlap, quiet=quiet)
        content = json.dumps(self.nodes, indent=4)
        try:
            with open(self.path, "w") as f:
                f.write(content)
        except PermissionError as exc:
            raise PermissionError(
                f"cannot write {self.path}: {exc.strerror}. flows.json has to be "
                f"writable by the user running nodered-mcp -- chown it to that user, "
                f"or point --flows-path somewhere writable."
            ) from exc
        print(f"Saved to {self.path}")

    def _check_layout(self, allow_overlap=False, quiet=False):
        """Compare the layout as loaded with the layout now; block on new errors."""
        try:
            before = layout.lint(self._layout_baseline())
        except (OSError, ValueError):
            return  # can't read the old state; nothing to diff against

        after = layout.lint(self.nodes)
        new = layout.new_findings(before, after)
        if not new:
            if not quiet:
                print("layout gate: no new layout problems")
            return

        errors = [f for f in new if f.severity == "ERROR"]
        warns = [f for f in new if f.severity != "ERROR"]
        for f in warns:
            print(f"layout gate: [WARN ] {f.check} [{f.tab}] {f.message}")
        if not errors:
            return

        for f in errors:
            print(f"layout gate: [ERROR] {f.check} [{f.tab}] {f.message}")
        if allow_overlap:
            print(f"layout gate: overridden, writing anyway ({len(errors)} errors)")
            return
        raise LayoutError(
            f"{len(errors)} new layout error(s) -- fix the placement, or call "
            f"save(allow_overlap=True) to write anyway. "
            f"Try f.free_region(tab_id, w, h) to claim empty canvas, or "
            f"f.refit_group(group_id) to resize a group around its nodes."
        )

    def deploy(self, allow_overlap=False, force=False):
        """Write flows.json, then make the running Node-RED pick it up.

        Two routes, chosen by whether an Admin API URL is configured:

        - `admin_url` set: POST the flows to Node-RED's own Admin API. It
          installs them in place, so nothing here needs a container runtime,
          a Docker socket, or even to be on the same host as Node-RED.
        - `admin_url` unset: the legacy route -- `docker cp` the flows into
          the container and run `restart_cmd`. Restarts the whole container,
          and only works where the Docker CLI does.

        Runs the same layout gate as save() either way, so a deploy that would
        introduce a new overlap is refused before anything leaves this process.

        Args:
            allow_overlap: Deploy even though the edit adds a layout error.
            force: Push over a newer revision. Only meaningful on the Admin API
                route, where it drops the `rev` so Node-RED skips its conflict
                check -- the local guard in session.py handles the file route.

        Raises:
            LayoutError: A new error-level layout problem was introduced.
            StaleFlowsError: Node-RED's flows moved on; pass force to override.
            DeployError: The flows never reached the running Node-RED.
        """
        if self.admin_url:
            if self.path:
                self.save(allow_overlap=allow_overlap, quiet=True)
            else:
                self._check_layout(allow_overlap=allow_overlap, quiet=True)
            self._deploy_admin_api(force=force)
            return
        self._deploy_docker(allow_overlap=allow_overlap)

    def _deploy_admin_api(self, force=False):
        """POST the in-memory flows to Node-RED's Admin API.

        The API version follows where the flows came from, so the staleness
        guard always matches the source of truth:

        - loaded from the Admin API: v2, echoing back the `rev` we were given.
          Node-RED answers 409 if anyone deployed in the meantime -- its own
          revision check, which is stronger than anything we could do here.
        - loaded from a file: v1, a bare node array. There is no rev to echo,
          and flows.json already has its own staleness guard in session.py.

        force drops the rev, which is how the API is told to skip the check.
        """
        headers = {"Node-RED-Deployment-Type": "full"}
        if self.rev is None or force:
            payload = self.nodes
        else:
            headers["Node-RED-API-Version"] = "v2"
            payload = {"rev": self.rev, "flows": self.nodes}

        status, body = self._admin_request("POST", "/flows", payload=payload, headers=headers)
        if self.rev is not None and not force:
            # v2 answers 200 with the revision the deploy produced. Adopt it,
            # or the next deploy in this session 409s against its own write.
            try:
                self.rev = json.loads(body).get("rev", self.rev)
            except ValueError:
                pass
        elif self.rev is not None:
            # The forced v1 deploy answered 204 with no rev. Re-read it, or the
            # next deploy in this session checks against a revision two writes old.
            _, self.rev = self.fetch_flows()
        print(f"Deployed {len(self.nodes)} nodes to {self.admin_url}/flows (HTTP {status})")

    def fetch_flows(self):
        """GET the live flows and their revision. Returns (nodes, rev).

        v2 so the response carries `rev`; without it there is nothing to give
        back on deploy and Node-RED cannot detect a conflicting write.
        """
        _, body = self._admin_request("GET", "/flows", headers={"Node-RED-API-Version": "v2"})
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise DeployError(
                f"{self.admin_url}/flows did not return JSON. Is that really a Node-RED Admin API?"
            ) from exc
        if isinstance(payload, list):  # a v1 runtime, or v2 not honoured
            return payload, None
        return payload.get("flows", []), payload.get("rev")

    def _admin_request(self, method, path, payload=None, headers=None):
        """One request against Node-RED's Admin API. Returns (status, body).

        Every way it can fail becomes either StaleFlowsError (409, somebody
        else deployed) or DeployError carrying something the caller can act on.
        """
        url = self.admin_url.rstrip("/") + path
        hdrs = dict(headers or {})
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            hdrs["Content-Type"] = "application/json"
        if self.admin_token:
            hdrs["Authorization"] = f"Bearer {self.admin_token}"
        elif self.admin_username:
            creds = f"{self.admin_username}:{self.admin_password}".encode()
            hdrs["Authorization"] = f"Basic {base64.b64encode(creds).decode()}"

        # Only pass a context when verification is off, so the default stays
        # whatever urllib would have done on its own.
        kwargs = {"context": _unverified_context()} if self.admin_insecure else {}
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=ADMIN_TIMEOUT, **kwargs) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise StaleFlowsError(
                    "Node-RED rejected the deploy: its flows have been changed by "
                    "someone else since this session loaded them -- most likely a "
                    "Deploy from the browser. Pushing now would revert that work.\n"
                    "  Either re-read with session(op='reload') and redo the edits, "
                    "or pass force=true to overwrite."
                ) from exc
            raise DeployError(_http_error_hint(exc, url)) from exc
        except urllib.error.URLError as exc:
            raise DeployError(
                f"could not reach the Node-RED Admin API at {url}: {exc.reason}. "
                f"Check --nodered-url, and that Node-RED is running and reachable "
                f"from this host."
            ) from exc
        except TimeoutError as exc:
            raise DeployError(
                f"the Node-RED Admin API at {url} did not answer within "
                f"{ADMIN_TIMEOUT:g}s. The request may still have landed -- check "
                f"Node-RED before retrying. Raise NODERED_TIMEOUT if this is normal "
                f"for your flow set."
            ) from exc

    def _deploy_docker(self, allow_overlap=False):
        """Copy flows.json into the container and restart it."""
        self._check_layout(allow_overlap=allow_overlap)
        # Write to a temp file first, then docker cp it in.
        fd, tmp = tempfile.mkstemp(prefix="flows_deploy_", suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.nodes, f, indent=4)
            self._run(["docker", "cp", tmp, f"{self.container}:{self.container_flows_path}"])
        finally:
            os.unlink(tmp)
        self._run(self.restart_cmd)
        print(f"Deployed and restarted Node-RED ({' '.join(self.restart_cmd)})")

    @staticmethod
    def _run(cmd):
        """Run a deploy subprocess, turning both ways it can fail into DeployError."""
        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError as exc:
            raise DeployError(
                f"{cmd[0]!r} is not on PATH. The container deploy route needs it -- "
                f"set --nodered-url to deploy over Node-RED's Admin API instead, "
                f"which needs no container runtime."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise DeployError(f"`{' '.join(cmd)}` failed with exit code {exc.returncode}.") from exc


def _unverified_context():
    """TLS without certificate checks, for a Node-RED reached by a name its cert does not cover."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _http_error_hint(exc, url):
    """Turn an HTTPError from the Admin API into something actionable."""
    detail = ""
    try:
        detail = exc.read().decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001 - the body is a nicety, never worth masking exc
        pass
    if exc.code == 401:
        return (
            f"Node-RED at {url} rejected the request as unauthorized. If adminAuth is "
            f"enabled, pass an access token with --nodered-token / NODERED_TOKEN. If a "
            f"proxy asks for a login (the Home Assistant add-on does), pass "
            f"--nodered-username and --nodered-password instead."
        )
    if exc.code == 404:
        return (
            f"no /flows endpoint at {url}. Check --nodered-url -- give the base URL "
            f"only (http://host:1880), and include httpAdminRoot if settings.js sets "
            f"one (http://host:1880/admin)."
        )
    return f"Node-RED at {url} returned HTTP {exc.code} {exc.reason}. {detail}".strip()
