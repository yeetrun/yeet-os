"""Enroll the OS and the Yeet server with independent persistent node identities."""
import contextlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from provision import SetupError, private_write, run


def validate_settings(settings, machine, server):
    if not isinstance(settings, dict) or set(settings) != {"osTag", "serviceTag", "autoServices"}:
        raise SetupError("Choose the machine tag, service tag and service networking default.")
    tags = [settings["osTag"], settings["serviceTag"], "tag:catch"]
    if any(not isinstance(tag, str) or not re.fullmatch(r"tag:[a-z][a-z0-9-]*", tag) for tag in tags):
        raise SetupError("Enter Tailscale tags such as tag:yeet-host and tag:app.")
    if len(set(tags)) != 3 or not isinstance(settings["autoServices"], bool):
        raise SetupError("Use separate tags for the machine, Yeet server and services.")
    if machine == server or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", server):
        raise SetupError("Give the Yeet server a distinct DNS name of at most 63 characters.")


def api(endpoint, *, method="POST", body=None, token=None):
    headers = {}
    if token:
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    req = Request("https://api.tailscale.com/api/v2/" + endpoint, method=method,
                  data=body, headers=headers)
    try:
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except HTTPError as exc:
        raise SetupError(f"Tailscale refused the request (HTTP {exc.code}). Check Auth Keys write and tag ownership.") from None
    except (URLError, TimeoutError) as exc:
        raise SetupError("Could not reach the Tailscale API. Check the installer network.") from exc


class Enrollment:
    def __init__(self, path):
        self.secret = path.read_text().strip()
        parts = self.secret.split("-", 3)
        if len(parts) != 4 or parts[:2] != ["tskey", "client"]:
            raise SetupError("Paste the OAuth client secret from Tailscale.")
        self.client_id = parts[2]

    @contextlib.contextmanager
    def key(self, tag):
        body = urlencode(dict(grant_type="client_credentials", scope="auth_keys", tags=tag,
                              client_id=self.client_id, client_secret=self.secret)).encode()
        token = api("oauth/token", body=body)["access_token"]
        caps = {"devices": {"create": {"tags": [tag], "preauthorized": True,
                                       "ephemeral": False, "reusable": False}}}
        try:
            result = api("tailnet/-/keys", token=token, body=json.dumps(
                {"capabilities": caps, "expirySeconds": 3600, "description": "Yeet OS enrollment"}
            ).encode())
        except SetupError as exc:
            raise SetupError(f"Cannot create a key for {tag}. {exc}") from None
        try:
            yield result["key"]
        finally:
            # Consumed one-time keys have already been revoked by Tailscale.
            try:
                api("tailnet/-/keys/" + result["id"], method="DELETE", token=token)
            except SetupError as exc:
                if "HTTP 404" not in str(exc):
                    raise

    def preflight(self, tags):
        for tag in tags:
            with self.key(tag):
                pass


def enroll_machine(state, hostname, keyfile):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir="/run", prefix="yeet-tailscale-") as temp:
        socket = Path(temp, "tailscaled.sock")
        cli = ["tailscale", "--socket=" + str(socket)]
        with subprocess.Popen(["tailscaled", "--tun=userspace-networking", "--port=0",
                               "--socket=" + str(socket), "--statedir=" + str(state),
                               "--state=" + str(state / "tailscaled.state")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as daemon:
            try:
                for _ in range(200):
                    if socket.exists():
                        break
                    if daemon.poll() is not None:
                        raise SetupError("The machine's Tailscale client could not start.")
                    time.sleep(.05)
                run(*cli, "up", "--timeout=90s", "--hostname=" + hostname,
                    "--auth-key=file:" + str(keyfile))
                status = json.loads(run(*cli, "status", "--json"))
                if status["BackendState"] != "Running":
                    raise SetupError("The machine has not connected to Tailscale.")
                return status["Self"]["DNSName"].rstrip(".")
            finally:
                daemon.terminate()


def connect(enrollment, settings, root, data, services, hostname, server, event, restore=False):
    from provision import enroll

    with tempfile.TemporaryDirectory(dir="/run", prefix="yeet-keys-") as temp:
        keyfile = Path(temp, "enroll.key")
        with enrollment.key(settings["osTag"]) as key:
            private_write(keyfile, key)
            name = enroll_machine(data / "os-tailscale", hostname, keyfile)
            event("progress", "Machine connected as " + name)
        if not restore:
            with enrollment.key("tag:catch") as key:
                private_write(keyfile, key)
                server = enroll(root, server, keyfile, event, data_dir=data)
        secret_dir = services / "catch/data"
        secret_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(secret_dir / "tailscale.key", enrollment.secret + "\n")
        return server
