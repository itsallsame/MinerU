"""Standard-library client for the same open business API used by the Web."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import mimetypes
import os
import re
import socket
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit


ALLOWED_API_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7",
))
REQUEST_KEY_RE = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")


def _approved_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(address in network for network in ALLOWED_API_NETWORKS)


class BusinessAPIError(Exception):
    def __init__(self, message: str, *, status: int | None = None, request_key: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.request_key = request_key


class BusinessClient:
    def __init__(self, base_url: str) -> None:
        try:
            parsed = urlsplit(base_url.strip())
            port = parsed.port
        except ValueError as exc:
            raise BusinessAPIError("Invalid business API URL") from exc
        if (
            parsed.scheme != "http" or not parsed.hostname or port is None
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment
        ):
            raise BusinessAPIError("Set MINERU_BUSINESS_API_URL to a plain internal HTTP host and port")
        host = parsed.hostname.lower()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if host != "localhost" and "." in host and not host.endswith((".internal", ".local", ".lan")):
                raise BusinessAPIError("Business API host must be local or on an internal network") from None
            self.literal_address: str | None = None
        else:
            if not _approved_address(address):
                raise BusinessAPIError("Business API address must be loopback or on an approved private network")
            self.literal_address = str(address)
        self.host = parsed.hostname
        self.port = port
        self.base_url = f"http://{parsed.netloc}"

    def evidence_url(self, evidence_id: str) -> str:
        return f"{self.base_url}/#evidence={quote(evidence_id, safe='')}"

    def _connect(self) -> http.client.HTTPConnection:
        if self.literal_address is not None:
            return http.client.HTTPConnection(self.literal_address, self.port, timeout=30)
        try:
            endpoints = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise BusinessAPIError(f"Business API host cannot be resolved: {exc}") from exc
        if not endpoints:
            raise BusinessAPIError("Business API host has no resolved address")
        addresses = [ipaddress.ip_address(endpoint[4][0]) for endpoint in endpoints]
        if not all(_approved_address(address) for address in addresses):
            raise BusinessAPIError("Business API hostname resolves outside approved private networks")
        return http.client.HTTPConnection(str(addresses[0]), self.port, timeout=30)

    @staticmethod
    def _read_response(connection: http.client.HTTPConnection) -> Any:
        response = connection.getresponse()
        raw = response.read()
        try:
            body = json.loads(raw) if raw else None
        except (ValueError, UnicodeDecodeError) as exc:
            raise BusinessAPIError(f"Business API returned non-JSON HTTP {response.status}", status=response.status) from exc
        if response.status >= 400:
            detail = body.get("detail") if isinstance(body, dict) else body
            raise BusinessAPIError(str(detail or "Business API request failed"), status=response.status)
        return body

    def request(self, method: str, path: str) -> Any:
        connection = self._connect()
        try:
            connection.request(method, f"/api/business{path}", headers={"Accept": "application/json"})
            return self._read_response(connection)
        except OSError as exc:
            raise BusinessAPIError(f"Business API unavailable: {exc}") from exc
        finally:
            connection.close()

    def upload(
        self, filename: Path, *, tier: str | None, template: str | None, request_key: str | None = None,
    ) -> Any:
        key = uuid.uuid4().hex if request_key is None else request_key
        if REQUEST_KEY_RE.fullmatch(key) is None:
            raise BusinessAPIError("Invalid upload idempotency key")
        if not filename.is_file():
            raise BusinessAPIError("Upload source must be an existing regular file")
        capabilities = self.request("GET", "/capabilities")
        extension = filename.suffix.lower().lstrip(".")
        if extension not in capabilities["parseable_extensions"]:
            raise BusinessAPIError(f"Unsupported document extension: .{extension}")
        if tier and (extension not in capabilities["tiered_extensions"] or tier not in capabilities["tiers"]):
            raise BusinessAPIError(f"Tier {tier} is not allowed for .{extension}")
        with filename.open("rb") as source:
            size = os.fstat(source.fileno()).st_size
            if size > capabilities["max_upload_bytes"]:
                raise BusinessAPIError("Document exceeds the business service upload limit")
            boundary = f"mineru-business-{uuid.uuid4().hex}"
            safe_name = filename.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
            mime = mimetypes.guess_type(filename.name)[0] or "application/octet-stream"
            prefix = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode()
            fields = b""
            for name, value in (("tier", tier), ("template_code", template)):
                if value is not None:
                    if "\r" in value or "\n" in value:
                        raise BusinessAPIError(f"Invalid {name}")
                    fields += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            suffix = f"\r\n{fields.decode()}--{boundary}--\r\n".encode()
            connection = self._connect()
            try:
                connection.putrequest("POST", "/api/business/documents")
                connection.putheader("Accept", "application/json")
                connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
                connection.putheader("Content-Length", str(len(prefix) + size + len(suffix)))
                connection.putheader("Idempotency-Key", key)
                connection.endheaders()
                connection.send(prefix)
                while chunk := source.read(1024 * 1024):
                    connection.send(chunk)
                connection.send(suffix)
                return self._read_response(connection)
            except BusinessAPIError as exc:
                if exc.status is not None and 200 <= exc.status < 300:
                    raise BusinessAPIError(
                        "Upload response unreadable; outcome unknown, retry only with the same request key",
                        status=exc.status, request_key=key,
                    ) from exc
                raise
            except OSError as exc:
                raise BusinessAPIError(
                    "Business API unavailable during upload; outcome unknown, retry only with the same request key",
                    request_key=key,
                ) from exc
            finally:
                connection.close()

    def overview(self, document_id: str) -> dict[str, Any]:
        encoded = quote(document_id, safe="")
        document = self.request("GET", f"/documents/{encoded}")
        revisions = self.request("GET", f"/documents/{encoded}/revisions")
        if not revisions:
            return {"document": document, "revision": None, "draft": None, "confirmed_for_latest_run": None}
        revision = revisions[0]
        runs = self.request("GET", f"/revisions/{quote(revision['id'], safe='')}/extractions")
        if not runs:
            return {"document": document, "revision": revision, "draft": None, "confirmed_for_latest_run": None}
        run_id = quote(runs[0]["id"], safe="")
        extraction = self.request("GET", f"/extractions/{run_id}")
        results = self.request("GET", f"/extractions/{run_id}/results")
        return {
            "document": document,
            "revision": revision,
            "draft": {"state": "machine_unconfirmed", **extraction},
            "confirmed_for_latest_run": results[0] if results else None,
        }


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(description="Open MinerU business document API client")
    main.add_argument("--base-url", default=os.environ.get("MINERU_BUSINESS_API_URL", ""), help="Approved business Web/API URL")
    commands = main.add_subparsers(dest="command", required=True)
    for command in ("capabilities", "templates", "documents"):
        commands.add_parser(command)
    upload = commands.add_parser("upload")
    upload.add_argument("file", type=Path)
    upload.add_argument("--tier", choices=("flash", "basic", "standard", "advanced"))
    upload.add_argument("--template")
    upload.add_argument("--request-key", help="Reuse this key when retrying an uncertain upload")
    upload.add_argument("--confirm-write", action="store_true", help="Acknowledge an explicitly requested upload")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)
    search_pages = commands.add_parser("search-pages")
    search_pages.add_argument("revision_id")
    search_pages.add_argument("query")
    search_pages.add_argument("--start-page", type=int)
    search_blocks = commands.add_parser("search-blocks")
    search_blocks.add_argument("revision_id")
    search_blocks.add_argument("query")
    search_blocks.add_argument("--start-page", type=int)
    outline = commands.add_parser("outline")
    outline.add_argument("revision_id")
    outline.add_argument("--start-page", type=int)
    structure = commands.add_parser("structure")
    structure.add_argument("revision_id")
    structure.add_argument("page_no", type=int)
    read = commands.add_parser("read")
    read.add_argument("revision_id")
    read.add_argument("locator")
    read.add_argument("--limit", type=int, default=12000)
    for command, argument in (
        ("task", "task_id"), ("revisions", "document_id"), ("overview", "document_id"),
        ("extract", "revision_id"), ("extraction", "run_id"), ("evidence", "evidence_id"),
        ("results", "run_id"), ("result", "result_id"),
    ):
        commands.add_parser(command).add_argument(argument)
    commands.choices["extract"].add_argument(
        "--confirm-write", action="store_true", help="Acknowledge an explicitly requested extraction run",
    )
    return main


def run(args: argparse.Namespace, client: BusinessClient) -> Any:
    command = args.command
    if command in ("capabilities", "templates", "documents"):
        return client.request("GET", f"/{command}")
    if command == "upload":
        if not args.confirm_write:
            raise BusinessAPIError("Upload requires --confirm-write after explicit user approval")
        request_key = uuid.uuid4().hex if args.request_key is None else args.request_key
        try:
            result = client.upload(args.file, tier=args.tier, template=args.template, request_key=request_key)
        except BusinessAPIError as exc:
            exc.request_key = request_key
            raise
        return {**result, "request_key": request_key} if isinstance(result, dict) else result
    if command == "overview":
        return client.overview(args.document_id)
    if command == "search":
        query = urlencode({"query": args.query, "limit": args.limit})
        return client.request("GET", f"/search?{query}")
    if command == "search-pages":
        params = {"query": args.query}
        if args.start_page is not None:
            params["start_page"] = args.start_page
        return client.request("GET", f"/revisions/{quote(args.revision_id, safe='')}/search?{urlencode(params)}")
    if command == "search-blocks":
        params = {"query": args.query}
        if args.start_page is not None:
            params["start_page"] = args.start_page
        return client.request("GET", f"/revisions/{quote(args.revision_id, safe='')}/search-blocks?{urlencode(params)}")
    if command == "outline":
        suffix = f"?{urlencode({'start_page': args.start_page})}" if args.start_page is not None else ""
        return client.request("GET", f"/revisions/{quote(args.revision_id, safe='')}/outline{suffix}")
    if command == "structure":
        query = urlencode({"page_no": args.page_no})
        return client.request("GET", f"/revisions/{quote(args.revision_id, safe='')}/structure?{query}")
    if command == "read":
        query = urlencode({"locator": args.locator, "limit": args.limit})
        return client.request("GET", f"/revisions/{quote(args.revision_id, safe='')}/content?{query}")
    if command == "task":
        return client.request("GET", f"/tasks/{quote(args.task_id, safe='')}")
    if command == "revisions":
        return client.request("GET", f"/documents/{quote(args.document_id, safe='')}/revisions")
    if command == "extract":
        if not args.confirm_write:
            raise BusinessAPIError("Extraction requires --confirm-write after explicit user approval")
        return client.request("POST", f"/revisions/{quote(args.revision_id, safe='')}/extractions")
    if command == "extraction":
        return {"state": "machine_unconfirmed", **client.request("GET", f"/extractions/{quote(args.run_id, safe='')}")}
    if command == "evidence":
        evidence = client.request("GET", f"/evidence/{quote(args.evidence_id, safe='')}")
        return {**evidence, "web_url": client.evidence_url(evidence["id"])}
    if command == "results":
        items = client.request("GET", f"/extractions/{quote(args.run_id, safe='')}/results")
        return {"state": "confirmed", "items": items}
    if command == "result":
        return {"state": "confirmed", "result": client.request("GET", f"/results/{quote(args.result_id, safe='')}")}
    raise BusinessAPIError("Unsupported command")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if not args.base_url:
            raise BusinessAPIError("MINERU_BUSINESS_API_URL or --base-url is required")
        result = run(args, BusinessClient(args.base_url))
    except BusinessAPIError as exc:
        failure = {"error": str(exc), "status": exc.status}
        if exc.request_key is not None:
            failure["request_key"] = exc.request_key
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
