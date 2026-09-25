"""Read-only runtime checks for the isolated business API and Doclib deployment.

Run from the repository root with ``python3 -m scripts.verify_business_runtime``.
This does not upload documents, prove physical air-gapping or assess model quality.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import HTTPRedirectHandler, Request, build_opener

from scripts import release_manifest
from scripts.verify_host_layout import check_host_separation


def _command(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=40).stdout.strip()


def _validated_bind(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = ipaddress.ip_address(value)
    if address.is_unspecified or not (address.is_loopback or address.is_private):
        raise ValueError("Business API bind address must be private or loopback")
    return address


def check_artifact_report(report: dict[str, Any], release: dict[str, Any], release_bytes: bytes) -> None:
    """Bind runtime acceptance to a prior, successful check of this exact release."""
    if not isinstance(release, dict) or not isinstance(release.get("model"), dict):
        raise ValueError("Selected release manifest is invalid")
    expected = {
        "release_manifest_sha256": hashlib.sha256(release_bytes).hexdigest(),
        "source_revision": release.get("source_revision"),
        "platform": release.get("platform"),
        "worker_image_id": release.get("worker_image_id"),
        "business_image_id": release.get("business_image_id"),
        "base_image_id": release.get("base_image_id"),
        "model_manifest_sha256": release.get("model", {}).get("manifest_sha256"),
    }
    if not isinstance(report, dict) or report.get("schema") != 1 or report.get("result") != "artifact_integrity_passed":
        raise ValueError("Artifact verification report is missing a successful result")
    if any(report.get(key) != value or value is None for key, value in expected.items()):
        raise ValueError("Artifact verification report differs from the selected release")
    if any(
        not isinstance(report.get(key), int) or isinstance(report[key], bool) or report[key] < 1
        for key in ("model_files_verified", "wheelhouse_files_verified", "web_assets_verified")
    ):
        raise ValueError("Artifact verification report has no verified artifact inventory")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise ValueError("Business API redirect refused")


def _container_info(container_id: str) -> dict[str, Any]:
    if not container_id:
        raise ValueError("Compose service has no container")
    data = json.loads(_command("docker", "container", "inspect", container_id, "--format", "{{json .}}"))
    if not isinstance(data, dict):
        raise ValueError("Docker container inspect did not return an object")
    return data


def _mount(container: dict[str, Any], destination: str, *, writable: bool) -> str:
    matches = [item for item in container.get("Mounts", []) if item.get("Destination") == destination]
    if len(matches) != 1 or matches[0].get("Type") != "bind" or matches[0].get("RW") is not writable:
        raise ValueError(f"Mount {destination} is missing or has the wrong read/write mode")
    source = matches[0].get("Source")
    if not isinstance(source, str) or not source.startswith("/"):
        raise ValueError(f"Mount {destination} has no absolute host source")
    return source


def _mount_layout(container: dict[str, Any], expected: set[str]) -> None:
    mounts = container.get("Mounts")
    if not isinstance(mounts, list) or any(not isinstance(item, dict) for item in mounts):
        raise ValueError("Container mount inventory is unavailable")
    destinations = [item.get("Destination") for item in mounts]
    if len(destinations) != len(expected) or set(destinations) != expected:
        raise ValueError("Container has an unexpected mount or is missing a required mount")


def _environment(container: dict[str, Any]) -> dict[str, str]:
    values = container.get("Config", {}).get("Env", [])
    if not isinstance(values, list):
        raise ValueError("Container environment is unavailable")
    return dict(item.split("=", 1) for item in values if isinstance(item, str) and "=" in item)


def check_runtime(
    *,
    release: dict[str, Any],
    business: dict[str, Any],
    worker: dict[str, Any],
    network: dict[str, Any],
    model_dir: Path,
    model_manifest: Path,
    business_bind: str,
    business_port: int,
    capabilities: dict[str, Any],
    doclib_status: dict[str, Any],
    cuda: dict[str, Any],
    host_gpu_lines: list[str],
) -> dict[str, Any]:
    if release.get("schema") != 4 or release.get("platform") != "linux/amd64":
        raise ValueError("Selected release is not a Linux amd64 release")
    _validated_bind(business_bind)
    if not (1 <= business_port <= 65535):
        raise ValueError("Business API port is invalid")
    for label, container, expected_id in (
        ("business", business, release.get("business_image_id")),
        ("worker", worker, release.get("worker_image_id")),
    ):
        if container.get("Image") != expected_id:
            raise ValueError(f"{label} container image differs from selected release")
        if not container.get("State", {}).get("Running"):
            raise ValueError(f"{label} container is not running")
        if container.get("HostConfig", {}).get("ReadonlyRootfs") is not True:
            raise ValueError(f"{label} root filesystem is not read-only")
    _mount_layout(business, {"/srv/mineru-inbox", "/var/lib/mineru-business"})
    _mount_layout(worker, {"/srv/mineru-inbox", "/var/lib/mineru", "/opt/mineru-models", "/etc/mineru/model-manifest.json"})
    business_shared = _mount(business, "/srv/mineru-inbox", writable=True)
    worker_shared = _mount(worker, "/srv/mineru-inbox", writable=False)
    if Path(business_shared).resolve() != Path(worker_shared).resolve():
        raise ValueError("Business and worker do not share the same original-file directory")
    business_data = _mount(business, "/var/lib/mineru-business", writable=True)
    doclib_data = _mount(worker, "/var/lib/mineru", writable=True)
    mounted_models = _mount(worker, "/opt/mineru-models", writable=False)
    mounted_manifest = _mount(worker, "/etc/mineru/model-manifest.json", writable=False)
    if Path(mounted_models).resolve() != model_dir.resolve() or Path(mounted_manifest).resolve() != model_manifest.resolve():
        raise ValueError("Worker model mounts differ from the verified release artifacts")
    check_host_separation(
        model_dir=model_dir,
        model_manifest=model_manifest,
        doclib_dir=Path(doclib_data),
        shared_documents_dir=Path(business_shared),
        business_dir=Path(business_data),
    )
    business_networks = business.get("NetworkSettings", {}).get("Networks", {})
    worker_networks = worker.get("NetworkSettings", {}).get("Networks", {})
    if (
        len(business_networks) != 1
        or len(worker_networks) != 1
        or set(business_networks) != set(worker_networks)
        or network.get("Internal") is not True
    ):
        raise ValueError("Containers are not confined to one shared internal network")
    network_name = next(iter(business_networks))
    network_id = network.get("Id")
    if not network_id or any(
        item.get("NetworkID") != network_id for item in (business_networks[network_name], worker_networks[network_name])
    ):
        raise ValueError("Container network identity differs from inspected internal network")
    worker_ports = worker.get("NetworkSettings", {}).get("Ports", {})
    if any(bindings for bindings in worker_ports.values()):
        raise ValueError("Doclib worker has a published host port")
    business_ports = business.get("NetworkSettings", {}).get("Ports", {})
    bindings = business_ports.get("8080/tcp")
    if (
        set(business_ports) != {"8080/tcp"}
        or not isinstance(bindings, list)
        or len(bindings) != 1
        or bindings[0].get("HostIp") != business_bind
        or bindings[0].get("HostPort") != str(business_port)
    ):
        raise ValueError("Business API published port differs from the approved bind")
    requests = worker.get("HostConfig", {}).get("DeviceRequests") or []
    if not any("gpu" in group for item in requests for group in item.get("Capabilities", [])):
        raise ValueError("Worker has no Docker GPU device request")
    worker_env = _environment(worker)
    business_env = _environment(business)
    required_worker = {
        "MINERU_MODEL_SOURCE": "local",
        "MINERU_MODEL_SMALL_BACKEND": "torch",
        "MINERU_MODEL_VLM_ENGINE": "vllm",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "MINERU_DOCLIB_REMOTE_DISABLED": "1",
        "MINERU_DOCLIB_TCP_ENABLED": "true",
        "MINERU_DOCLIB_TCP_STRICT_PORT": "true",
        "MINERU_LLM_AIDED_FEATURES_TITLE_LEVELING": "false",
        "MINERU_LLM_AIDED_FEATURES_CROSS_PAGE_TABLE_CELL_MERGE": "false",
    }
    if any(worker_env.get(key) != value for key, value in required_worker.items()) or worker_env.get(
        "MINERU_MODEL_VLM_SERVER_URL"
    ):
        raise ValueError("Worker is not configured for local offline models")
    required_worker_paths = {
        "MINERU_HOME": "/var/lib/mineru",
        "MINERU_MODEL_BASE_DIR": "/opt/mineru-models",
        "MINERU_MODEL_MANIFEST": "/etc/mineru/model-manifest.json",
    }
    if any(worker_env.get(key) != value for key, value in required_worker_paths.items()):
        raise ValueError("Worker persistent paths differ from the verified mounts")
    if worker_env.get("MINERU_DOCLIB_COMPACTION_INTERVAL_SEC") != "0":
        raise ValueError("Worker history retention differs from the versioned-evidence deployment")
    if worker_env.get("MINERU_EXPECTED_MODEL_MANIFEST_SHA256") != release.get("model", {}).get("manifest_sha256"):
        raise ValueError("Worker model manifest binding differs from selected release")
    if (
        business_env.get("HF_HUB_OFFLINE") != "1"
        or business_env.get("TRANSFORMERS_OFFLINE") != "1"
        or business_env.get("HF_DATASETS_OFFLINE") != "1"
        or business_env.get("MINERU_BUSINESS_DOCLIB_URL") != "http://doclib-worker:15980"
    ):
        raise ValueError("Business API offline or internal Doclib configuration differs")
    required_business_paths = {
        "MINERU_BUSINESS_DB_PATH": "/var/lib/mineru-business/business.sqlite3",
        "MINERU_BUSINESS_UPLOAD_ROOT": "/srv/mineru-inbox",
        "MINERU_BUSINESS_WEB_ROOT": "/opt/mineru/business-web/dist",
        "MINERU_BUSINESS_REQUIRE_WEB": "1",
    }
    if any(business_env.get(key) != value for key, value in required_business_paths.items()):
        raise ValueError("Business API persistence paths or required Web root differ from the verified mounts")
    if (
        not isinstance(capabilities, dict)
        or not capabilities.get("parseable_extensions")
        or capabilities.get("max_upload_bytes", 0) < 1
    ):
        raise ValueError("Business API did not return usable capabilities")
    parse_server = doclib_status.get("parse_server") if isinstance(doclib_status, dict) else None
    remote = parse_server.get("remote") if isinstance(parse_server, dict) else None
    if (
        not isinstance(doclib_status, dict)
        or doclib_status.get("running") is not True
        or doclib_status.get("tcp", {}).get("enabled") is not True
        or doclib_status.get("tcp", {}).get("port") != 15980
        or doclib_status.get("workers", {}).get("parse_running") is not True
        or not isinstance(remote, dict)
        or remote.get("url")
    ):
        raise ValueError("Internal Doclib is unavailable or has remote parsing configured")
    if cuda.get("available") is not True or not isinstance(cuda.get("count"), int) or cuda["count"] < 1:
        raise ValueError("Worker CUDA is unavailable")
    if not host_gpu_lines or len(host_gpu_lines) < cuda["count"]:
        raise ValueError("Host NVIDIA GPU/driver probe is unavailable or inconsistent")
    return {
        "schema": 1,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_revision": release["source_revision"],
        "worker_image_id": release["worker_image_id"],
        "business_image_id": release["business_image_id"],
        "business_bind": business_bind,
        "business_port": business_port,
        "internal_network": network_name,
        "host_gpu_count": len(host_gpu_lines),
        "worker_cuda_device_count": cuda["count"],
        "doclib_version": doclib_status.get("version"),
        "result": "runtime_preflight_passed",
    }


_DOCLIB_PROBE = """import json, urllib.request
with urllib.request.urlopen('http://doclib-worker:15980/api/v1/server/status', timeout=10) as response:
    raw = response.read(2 * 1024 * 1024 + 1)
if len(raw) > 2 * 1024 * 1024:
    raise RuntimeError('Doclib status response is too large')
status = json.loads(raw)
print(json.dumps({key: status.get(key) for key in ('running', 'version', 'tcp', 'workers', 'parse_server')}))
"""
_CUDA_PROBE = """import json, torch
print(json.dumps({'available': bool(torch.cuda.is_available()), 'count': int(torch.cuda.device_count())}))
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--artifact-report", type=Path, required=True)
    parser.add_argument("--compose-file", type=Path, default=Path("docker/compose.business.yaml"))
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--business-bind", required=True)
    parser.add_argument("--business-port", type=int, default=8088)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        address = _validated_bind(args.business_bind)
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("Runtime report already exists; choose a new output path")
        if args.output.resolve().is_relative_to(args.model_dir.resolve()) or args.output.resolve() in {
            args.release.resolve(),
            args.model_manifest.resolve(),
            args.artifact_report.resolve(),
        }:
            raise ValueError("Runtime report must be outside model and release artifacts")
        if not args.release.is_file() or args.release.is_symlink():
            raise ValueError("Selected release manifest is missing or is a symlink")
        if not args.artifact_report.is_file() or args.artifact_report.is_symlink():
            raise ValueError("Artifact verification report is missing or is a symlink")
        release_bytes = args.release.read_bytes()
        release = json.loads(release_bytes)
        artifact_bytes = args.artifact_report.read_bytes()
        check_artifact_report(json.loads(artifact_bytes), release, release_bytes)
        service_ids = {
            service: _command("docker", "compose", "-f", str(args.compose_file), "ps", "-q", service)
            for service in ("business-api", "doclib-worker")
        }
        business = _container_info(service_ids["business-api"])
        worker = _container_info(service_ids["doclib-worker"])
        networks = business.get("NetworkSettings", {}).get("Networks", {})
        if len(networks) != 1:
            raise ValueError("Business container must have one network")
        network_name = next(iter(networks))
        network = json.loads(_command("docker", "network", "inspect", network_name, "--format", "{{json .}}"))
        host = f"[{address}]" if address.version == 6 else str(address)
        origin = f"http://{host}:{args.business_port}"
        with build_opener(_NoRedirect()).open(
            Request(f"{origin}/api/business/capabilities", headers={"Accept": "application/json"}),
            timeout=10,
        ) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Business API capabilities response is too large")
        capabilities = json.loads(raw)
        doclib_status = json.loads(_command("docker", "exec", service_ids["business-api"], "python3", "-c", _DOCLIB_PROBE))
        cuda = json.loads(_command("docker", "exec", service_ids["doclib-worker"], "python3", "-c", _CUDA_PROBE))
        host_gpu_lines = _command(
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ).splitlines()
        report = check_runtime(
            release=release,
            business=business,
            worker=worker,
            network=network,
            model_dir=args.model_dir,
            model_manifest=args.model_manifest,
            business_bind=args.business_bind,
            business_port=args.business_port,
            capabilities=capabilities,
            doclib_status=doclib_status,
            cuda=cuda,
            host_gpu_lines=host_gpu_lines,
        )
        report["artifact_report_sha256"] = hashlib.sha256(artifact_bytes).hexdigest()
        release_manifest._write_new_release(args.output, report)
        print("Business runtime preflight passed; physical isolation and real parsing remain separate gates")
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"Business runtime preflight error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
