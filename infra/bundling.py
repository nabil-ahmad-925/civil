"""Builds one Lambda package per function: its Python dependencies for Lambda's Linux/arm64, the part of server/app that
function imports, the shared helper and its handler. Runs pip on this machine (no Docker needed); Docker is the fallback.

lambdas/<name>/handler.py     the function (entry point: handler.handler); any other .py in the folder is copied with it
lambdas/<name>/requirements.txt   its dependencies (none for health: it is shipped as is)
lambdas/shared/lambda_common.py   copied next to each handler that needs it
"""

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import jsii
from aws_cdk import AssetHashType, BundlingOptions, DockerImage, ILocalBundling
from aws_cdk import aws_lambda as lambda_

INFRA = Path(__file__).resolve().parent
LAMBDAS = INFRA / "lambdas"
SERVER_APP = INFRA.parent / "server" / "app"
SHARED = LAMBDAS / "shared" / "lambda_common.py"
PYTHON_VERSION = "3.13"

# What each function imports from server/app. The PDF reading, chunking and FastAPI parts stay out of every package.
_BASE = [
    "__init__.py", "config.py", "models.py",
    "repository/__init__.py", "repository/base.py", "repository/keywords.py", "repository/memory.py",
    "repository/paradedb.py", "repository/supabase_repo.py",
    "ingestion/__init__.py", "ingestion/embeddings.py",
]


@dataclass(frozen=True)
class LambdaSpec:
    name: str  # folder under lambdas/
    server_files: tuple[str, ...] = ()
    uses_shared: bool = True

    @property
    def folder(self) -> Path:
        return LAMBDAS / self.name

    @property
    def has_dependencies(self) -> bool:
        return (self.folder / "requirements.txt").exists()


CHAT = LambdaSpec("chat", tuple(_BASE))  # chat_service.py, retrieval.py and llm_client.py live in lambdas/chat/ itself
HEALTH = LambdaSpec("health", (), uses_shared=False)  # pure standard library: deployed from its folder as is


def copy_sources(spec: LambdaSpec, out: Path) -> None:
    for rel in spec.server_files:
        dest = out / "app" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SERVER_APP / rel, dest)
    if spec.uses_shared:
        shutil.copy2(SHARED, out / "lambda_common.py")
    for py in spec.folder.glob("*.py"):  # handler.py and the function's own modules
        shutil.copy2(py, out / py.name)


@jsii.implements(ILocalBundling)
class PipBundling:
    def __init__(self, spec: LambdaSpec) -> None:
        self.spec = spec

    def try_bundle(self, output_dir: str, options) -> bool:  # noqa: ANN001
        out = Path(output_dir)
        try:
            subprocess.run(
                [
                    sys.executable, "-m", "pip", "install", "-r", str(self.spec.folder / "requirements.txt"),
                    "--target", str(out), "--platform", "manylinux2014_aarch64", "--only-binary=:all:",
                    "--python-version", PYTHON_VERSION, "--implementation", "cp", "--upgrade", "--quiet",
                ],
                check=True,
            )
        except (subprocess.CalledProcessError, OSError):
            return False  # fall back to Docker
        copy_sources(self.spec, out)
        return True


def lambda_code(spec: LambdaSpec) -> lambda_.Code:
    if not spec.has_dependencies:
        return lambda_.Code.from_asset(str(spec.folder))
    copies = [f"mkdir -p /asset-output/app/$(dirname {f}) && cp server/app/{f} /asset-output/app/{f}" for f in spec.server_files]
    docker_cmd = " && ".join(
        [
            f"pip install -r infra/lambdas/{spec.name}/requirements.txt -t /asset-output --quiet",
            *copies,
            "cp infra/lambdas/shared/lambda_common.py /asset-output/lambda_common.py",
            f"cp infra/lambdas/{spec.name}/*.py /asset-output/",
        ]
    )
    return lambda_.Code.from_asset(
        str(INFRA.parent),
        asset_hash_type=AssetHashType.SOURCE,
        exclude=[
            "client", "storage", ".git", "**/.venv", "**/node_modules", "**/__pycache__", "infra/cdk.out", "infra/.venv",
            "infra/tests", "server/tests", "server/docs", "server/sql",
        ],
        bundling=BundlingOptions(
            image=DockerImage.from_registry("public.ecr.aws/sam/build-python3.13"),
            platform="linux/arm64",
            command=["bash", "-c", docker_cmd],
            local=PipBundling(spec),
        ),
    )
