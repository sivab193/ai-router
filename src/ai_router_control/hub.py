"""Model hub: registry, packaging, and deploy targets (Phase 6).

The hub is his curated shelf of models — not a public bazaar, not a
training platform, not a second router. Serving always goes through
ai-router; the hub only provisions the runner that serves.

This module is pure domain logic: target catalog, fit checks, quotes,
Kitfile/manifest rendering, and cloud provisioning command construction.
It performs no I/O except through the caller's subprocess/HTTP clients.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


MODEL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Hourly costs are estimates checked 2026-11-20. The deploy confirmation
# always labels them as estimates; verify against the provider before
# a long run. $0.00 means the owner already pays for the hardware.
PRICING_SOURCE = "estimate-2026-11-20"

# vLLM server image for cloud deploys. Override with AI_ROUTER_VLLM_IMAGE.
VLLM_IMAGE_DEFAULT = "vllm/vllm-openai:latest"

# GCE deep-learning image family: CUDA + Docker + NVIDIA container
# toolkit preinstalled, so the startup script only runs vLLM + runner.
GCP_IMAGE_FAMILY = "pytorch-latest-gpu"
GCP_IMAGE_PROJECT = "deeplearning-platform-release"


@dataclass(frozen=True, slots=True)
class DeployTarget:
    id: str
    name: str
    gpu: str
    vram_mib: int
    hourly_cost_usd: float
    # "gcloud": the hub can provision it when the gcloud CLI is present.
    # "manual": the hub prepares the command/script; the operator runs it.
    provision: str
    ephemeral: bool = False
    notes: str = ""


TARGETS: dict[str, DeployTarget] = {
    "gcp-l4": DeployTarget(
        id="gcp-l4",
        name="GCP · L4",
        gpu="NVIDIA L4",
        vram_mib=24 * 1024,
        hourly_cost_usd=0.71,
        provision="gcloud",
        notes="GCE g2-standard-4 + 1x L4. Covered by Google Cloud for Startups credit.",
    ),
    "gcp-a100-40": DeployTarget(
        id="gcp-a100-40",
        name="GCP · A100 40GB",
        gpu="NVIDIA A100 40GB",
        vram_mib=40 * 1024,
        hourly_cost_usd=1.60,
        provision="gcloud",
        notes="GCE a2-highgpu-1g + 1x A100 40GB.",
    ),
    "gcp-a100-80": DeployTarget(
        id="gcp-a100-80",
        name="GCP · A100 80GB",
        gpu="NVIDIA A100 80GB",
        vram_mib=80 * 1024,
        hourly_cost_usd=2.10,
        provision="gcloud",
        notes="GCE a2-highgpu-1g + 1x A100 80GB.",
    ),
    "runpod-4090": DeployTarget(
        id="runpod-4090",
        name="RunPod · RTX 4090",
        gpu="NVIDIA RTX 4090",
        vram_mib=24 * 1024,
        hourly_cost_usd=0.34,
        provision="manual",
        notes="RunPod community pricing. The hub prepares the setup script; the operator starts the pod.",
    ),
    "vast-4090": DeployTarget(
        id="vast-4090",
        name="Vast.ai · RTX 4090",
        gpu="NVIDIA RTX 4090",
        vram_mib=24 * 1024,
        hourly_cost_usd=0.47,
        provision="manual",
        notes="Vast.ai median pricing. The hub prepares the setup script; the operator starts the instance.",
    ),
    "colab-t4": DeployTarget(
        id="colab-t4",
        name="Colab · T4 (free)",
        gpu="NVIDIA Tesla T4",
        vram_mib=15 * 1024,
        hourly_cost_usd=0.0,
        provision="manual",
        ephemeral=True,
        notes="Free-tier Colab GPU lottery — usually a 16 GB T4, sometimes better. Sessions can end without warning; inference is stateless, so this is a warning, not a block.",
    ),
    "fleet-rog": DeployTarget(
        id="fleet-rog",
        name="Fleet · ROG laptop",
        gpu="NVIDIA RTX 3060 Laptop",
        vram_mib=6 * 1024,
        hourly_cost_usd=0.0,
        provision="manual",
        notes="His Windows gaming laptop. Native Windows agent; models live on E:\\models.",
    ),
    "fleet-mini": DeployTarget(
        id="fleet-mini",
        name="Fleet · Mac mini",
        gpu="Apple M4 (shared memory)",
        vram_mib=16 * 1024,
        hourly_cost_usd=0.0,
        provision="manual",
        notes="16 GB unified memory shared with the OS; treat the full 16 GB as an upper bound, not usable VRAM.",
    ),
}

# Targets that live outside his network and therefore cannot reach local
# weights. A cloud deploy needs a Hugging Face source or a ModelKit ref.
CLOUD_TARGETS = ("gcp-l4", "gcp-a100-40", "gcp-a100-80", "runpod-4090", "vast-4090", "colab-t4")


def validate_model_id(value: str) -> str:
    normalized = value.strip().lower()
    if not MODEL_ID_PATTERN.fullmatch(normalized):
        raise ValueError("model id must be lowercase letters, digits, . - or _, up to 128 chars")
    return normalized


def validate_version(value: str) -> str:
    normalized = value.strip()
    if not VERSION_PATTERN.fullmatch(normalized):
        raise ValueError("version must be letters, digits, . - or _, up to 128 chars")
    return normalized


def deploy_alias(model_id: str, version: str) -> str:
    """Router alias for a deployed hub version: ``local/<model>-<version>``."""
    return f"local/{model_id}-{version}"


def instance_name(deployment_id: str) -> str:
    """GCE instance name for a deployment (lowercase, hyphens only)."""
    return f"ai-router-{deployment_id}".replace("_", "-")


def fit_check(min_vram_mib: int | None, target: DeployTarget) -> tuple[str, str]:
    """Check a model version against a target GPU.

    Returns (verdict, reason) where verdict is "fits", "unknown", or
    "refused". Unknown minimum VRAM never blocks by itself — it warns
    and requires an explicit override at deploy time.
    """
    if min_vram_mib is None:
        return (
            "unknown",
            f"Minimum VRAM is not recorded for this version; {target.gpu} has "
            f"{target.vram_mib // 1024} GB. Proceed only if you know it fits.",
        )
    if min_vram_mib <= target.vram_mib:
        return (
            "fits",
            f"Needs {min_vram_mib // 1024} GB; {target.gpu} has {target.vram_mib // 1024} GB.",
        )
    return (
        "refused",
        f"Needs {min_vram_mib // 1024} GB but {target.gpu} has only "
        f"{target.vram_mib // 1024} GB. Pick a larger target or a smaller quantization.",
    )


def source_reachable_from_target(
    source_type: str, modelkit_ref: str | None, target_id: str
) -> tuple[bool, str]:
    """Cloud targets cannot fetch weights from his NAS, Drive, or ROG disk.

    They need a Hugging Face source (public download) or a ModelKit ref in
    an OCI registry the target can reach. Fleet targets can use local paths.
    """
    if target_id not in CLOUD_TARGETS:
        return True, "Fleet target: local weights are reachable."
    if source_type == "huggingface":
        return True, "Hugging Face source: downloadable from the cloud target."
    if modelkit_ref:
        return True, "ModelKit ref: pullable from the configured OCI registry."
    return (
        False,
        "Weights are local-only (no Hugging Face source, no ModelKit ref). "
        "Mirror the model to a registry first, or deploy to a fleet target.",
    )


def render_kitfile(model: dict, version: dict) -> str:
    """Render a KitOps Kitfile (v1) for a registry version.

    The operator runs ``kit pack`` / ``kit push`` with this file; the
    control plane does not shell out to the kit CLI. The Kitfile carries
    the same metadata as the registry record so the packaged artifact
    stays self-describing.
    """
    lines = [
        "# KitOps Kitfile generated by ai-router model hub.",
        "# Pack: kit pack . -t <registry>/<model>:<version>",
        "# Push: kit push <registry>/<model>:<version>",
        "manifestVersion: v1.0.0",
        "package:",
        f"  name: {model['id']}",
        f"  description: {model.get('display_name') or model['id']}",
        f"  license: {model.get('license') or 'unknown'}",
        "model:",
        f"- name: {model['id']}",
        f"  path: {version.get('kit_path') or '.'}",
        f"  description: {model.get('display_name') or model['id']} ({version['version']})",
        f"  license: {model.get('license') or 'unknown'}",
    ]
    if version.get("context_length"):
        lines.append(f"  # context_length: {version['context_length']}")
    if version.get("min_vram_mib"):
        lines.append(f"  # min_vram_mib: {version['min_vram_mib']}")
    return "\n".join(lines) + "\n"


def render_model_manifest(model: dict, version: dict) -> dict:
    """Plain ``model.json`` manifest (the no-KitOps fallback layout).

    One directory per model version on the NAS holds this file next to
    the weights. Same fields as the registry record.
    """
    return {
        "model_id": model["id"],
        "display_name": model.get("display_name"),
        "version": version["version"],
        "source_type": model["source_type"],
        "source": model["source"],
        "source_revision": version.get("source_revision"),
        "license": model.get("license"),
        "format": model["format"],
        "quantization": version.get("quantization"),
        "size_bytes": version.get("size_bytes"),
        "context_length": version.get("context_length"),
        "min_vram_mib": version.get("min_vram_mib"),
        "runtime": version["runtime"],
        "sha256": version.get("sha256"),
        "modelkit_ref": version.get("modelkit_ref"),
        "locations": version.get("locations", []),
    }


def render_gcp_startup_script(
    *,
    public_url: str,
    runner_name: str,
    model_source: str,
    served_name: str,
    model_dir: str = "/opt/models/current",
    vllm_image: str = VLLM_IMAGE_DEFAULT,
    max_model_len: int | None = None,
) -> str:
    """Startup script for a GCE GPU VM.

    Fetches the enrollment token from instance metadata (single-use,
    with the expiry the deploy set — two hours for hub deploys, to allow
    model download time), downloads the model from Hugging Face, starts
    vLLM in Docker bound to loopback, then enrolls the ai-router runner.
    The script carries no provider credentials — only the public control
    plane URL and the short-lived enrollment token.
    """
    max_len_args = f" --max-model-len {max_model_len}" if max_model_len else ""
    return f"""#!/bin/bash
# ai-router hub deploy: vLLM + runner enrollment. Generated, do not edit by hand.
set -euo pipefail
METADATA="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
TOKEN="$(curl -fsS -H 'Metadata-Flavor: Google' "$METADATA/ai-router-enrollment-token")"
PUBLIC_URL="{public_url}"
MODEL="{model_source}"
MODEL_DIR="{model_dir}"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-pip curl > /dev/null
python3 -m pip install -q --break-system-packages huggingface_hub

mkdir -p "$MODEL_DIR"
python3 -m huggingface_hub.commands.huggingface_cli download "$MODEL" --local-dir "$MODEL_DIR" --local-dir-use-symlinks False

docker run -d --restart unless-stopped --gpus all --network host \\
  -v "$MODEL_DIR:/model:ro" \\
  {vllm_image} \\
  --model /model --host 127.0.0.1 --port 8000 \\
  --served-model-name "{served_name}"{max_len_args}

for attempt in $(seq 1 60); do
  curl -fsS http://127.0.0.1:8000/health > /dev/null 2>&1 && break
  sleep 10
done

curl -fsSL "$PUBLIC_URL/install/runner.sh" | bash -s -- --token "$TOKEN" --name "{runner_name}"
"""


def gcp_create_command(
    *,
    instance_name: str,
    zone: str,
    target: DeployTarget,
    project: str | None,
    startup_script_path: str,
    enrollment_token: str,
    machine_type: str | None = None,
) -> list[str]:
    """Build the `gcloud compute instances create` command for a target.

    The enrollment token rides in instance metadata, where the startup
    script reads it back. It is single-use with a short expiry, and it
    is the only secret the VM ever sees — no provider credentials.
    """
    accelerator = {
        "gcp-l4": ("nvidia-l4", 1, "g2-standard-4"),
        "gcp-a100-40": ("nvidia-tesla-a100", 1, "a2-highgpu-1g"),
        "gcp-a100-80": ("nvidia-tesla-a100-80gb", 1, "a2-highgpu-1g"),
    }.get(target.id)
    if accelerator is None:
        raise ValueError(f"target {target.id} is not a GCP target")
    gpu_type, gpu_count, default_machine = accelerator
    command = [
        "gcloud", "compute", "instances", "create", instance_name,
        "--zone", zone,
        "--machine-type", machine_type or default_machine,
        "--accelerator", f"type={gpu_type},count={gpu_count}",
        "--image-family", GCP_IMAGE_FAMILY,
        "--image-project", GCP_IMAGE_PROJECT,
        "--boot-disk-size", "100GB",
        "--metadata-from-file", f"startup-script={startup_script_path}",
        "--metadata", f"ai-router-enrollment-token={enrollment_token}",
    ]
    if project:
        command += ["--project", project]
    return command


def gcp_delete_command(instance_name: str, zone: str, project: str | None) -> list[str]:
    command = ["gcloud", "compute", "instances", "delete", instance_name, "--zone", zone, "--quiet"]
    if project:
        command += ["--project", project]
    return command


def gpu_cost_for_day(
    deployments: list[dict],
    profile_id: str,
    day_start_ts: float,
    day_end_ts: float,
) -> float:
    """Allocated GPU cost for one profile on one UTC day.

    Each deployment accrues hourly_cost_usd for the overlap between its
    lifetime (created_at to torn_down_at, or now for live ones) and the
    day window. Deployments in another profile, or still only quoted,
    accrue nothing.
    """
    total = 0.0
    for deployment in deployments:
        if deployment["profile_id"] != profile_id:
            continue
        if deployment["status"] in ("quoted", "failed"):
            continue
        start = max(float(deployment["created_at"]), day_start_ts)
        end = deployment.get("torn_down_at")
        end_ts = float(end) if end else day_end_ts
        end_ts = min(end_ts, day_end_ts)
        if end_ts <= start:
            continue
        total += (end_ts - start) / 3600.0 * float(deployment["hourly_cost_usd"])
    return total


@dataclass(frozen=True, slots=True)
class SeedVersion:
    version: str
    source_revision: str | None = None
    quantization: str | None = None
    size_bytes: int | None = None
    context_length: int | None = None
    min_vram_mib: int | None = None
    runtime: str = "vllm"
    sha256: str | None = None
    modelkit_ref: str | None = None
    locations: tuple[dict, ...] = ()


@dataclass(frozen=True, slots=True)
class SeedModel:
    id: str
    display_name: str
    source_type: str
    source: str
    license: str | None
    format: str
    status: str = "active"
    notes: str = ""
    versions: tuple[SeedVersion, ...] = ()


# First residents (§4.6 of the handoff). Sizes marked None are unknown
# exactly; the approximate figure lives in notes, never as a fake precise
# number. The Aayvu fine-tuned model is a planned entry until training
# produces weights.
SEED_MODELS: tuple[SeedModel, ...] = (
    SeedModel(
        id="qwen-image-2.1",
        display_name="Qwen-Image 2.1 (Uncensored, Q8_0)",
        source_type="huggingface",
        source="LxBarn/Qwen-Image-2.1-Uncensored-Quantized",
        license="Qwen Research License (non-commercial)",
        format="gguf",
        notes="Transformer only, ~7.59 GB. The Qwen3-VL 8B text encoder + VAE from the same repo are still needed for a full ComfyUI run.",
        versions=(
            SeedVersion(
                version="q8_0",
                quantization="Q8_0",
                min_vram_mib=6 * 1024,
                runtime="comfyui",
                locations=(
                    {"kind": "rog", "path": "E:\\models\\qwen-image-2.1-UC-Q8_0.gguf"},
                ),
            ),
        ),
    ),
    SeedModel(
        id="qwen3-coder-30b",
        display_name="Qwen3-Coder-30B-A3B-Instruct",
        source_type="huggingface",
        source="Qwen/Qwen3-Coder-30B-A3B-Instruct",
        license="Apache-2.0",
        format="safetensors",
        notes="Reference vLLM model from the ai-router README. ~60 GB in bf16, ~30 GB in FP8; minimum VRAM depends on quantization, so it stays unknown until a version pins one.",
        versions=(
            SeedVersion(
                version="bf16",
                quantization="bf16",
                runtime="vllm",
                locations=(),
            ),
        ),
    ),
    SeedModel(
        id="deepseek-v4.1-flash",
        display_name="DeepSeek V4.1 Flash 1017 Uncensored (FP8)",
        source_type="local",
        source="Google Drive /MyDrive/LLMs (ARTEX Colab download)",
        license="DeepSeek license — confirm before serving",
        format="fp8",
        notes="~510 GB, 48 shards. Multi-GPU class weights; not deployable on a single small GPU. Confirm the exact Hugging Face repo id before a cloud deploy.",
        versions=(
            SeedVersion(
                version="fp8",
                quantization="FP8",
                runtime="vllm",
                locations=(
                    {"kind": "drive", "path": "MyDrive/LLMs/DeepSeek-V4.1-Flash-1017-Uncensored-FP8"},
                ),
            ),
        ),
    ),
    SeedModel(
        id="aayvu-ft",
        display_name="Aayvu fine-tuned model (planned)",
        source_type="local",
        source="pending — trains in the compute pool",
        license=None,
        format="safetensors",
        status="planned",
        notes="Placeholder until the Aayvu LoRA/QLoRA fine-tune produces weights. Training runs in the compute pool; the result lands here.",
        versions=(),
    ),
)
