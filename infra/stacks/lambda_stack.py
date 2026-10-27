import json

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from bundling import CHAT, HEALTH, LambdaSpec, lambda_code

SECRET_NAME = "civil-eng/chat"


class LambdaStack(Stack):
    """The two functions (chat, health) and the secret the chat function reads its credentials from."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:  # noqa: ANN003
        super().__init__(scope, construct_id, **kwargs)
        ctx = self.node.try_get_context

        # One secret holds the credentials. CHAT_API_KEY is generated here; the rest are filled in afterwards
        # with infra/scripts/set_secrets.py, so no credential is ever in the template or in git.
        self.secret = secretsmanager.Secret(
            self, "AppSecret", secret_name=SECRET_NAME,
            description="OpenAI and Supabase credentials and the API key for the chat API.",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({"OPENAI_API_KEY": "", "SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": ""}),
                generate_string_key="CHAT_API_KEY", exclude_punctuation=True, password_length=48,
            ),
        )

        env = {
            "SECRET_ARN": self.secret.secret_arn,
            "REPOSITORY": "supabase",
            "STORAGE_BUCKET": ctx("storage_bucket") or "manuals",  # the Supabase bucket the figures are in
            "EMBEDDING_PROVIDER": "openai",
            "EMBEDDING_MODEL": ctx("embedding_model") or "text-embedding-3-small",
            "EMBEDDING_DIM": str(ctx("embedding_dim") or 1536),
            "CHAT_MODEL": ctx("chat_model") or "gpt-4.1-mini",
            # "paradedb" once the ParadeDB database is set up and PARADEDB_URL is in the secret
            # (cdk deploy -c keyword_backend=paradedb); see server/docs/INGESTION_FLOW.md, "ParadeDB".
            "KEYWORD_BACKEND": ctx("keyword_backend") or "fts",
        }

        def function(name: str, spec: LambdaSpec, timeout: int, memory: int, configured: bool = True) -> lambda_.Function:
            if ctx("bundle") == "false":  # unit tests: skip building the dependencies
                code = lambda_.Code.from_asset(str(spec.folder))
            else:
                code = lambda_code(spec)
            fn = lambda_.Function(
                self, name, function_name=f"civil-eng-{name.lower()}", runtime=lambda_.Runtime.PYTHON_3_13,
                architecture=lambda_.Architecture.ARM_64, handler="handler.handler", code=code,
                timeout=Duration.seconds(timeout), memory_size=memory, environment=env if configured else None,
                log_group=logs.LogGroup(self, f"{name}Logs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=RemovalPolicy.DESTROY),
            )
            if configured:
                self.secret.grant_read(fn)
            return fn

        self.chat = function("Chat", CHAT, timeout=60, memory=512)  # retrieval + one model call
        self.health = function("Health", HEALTH, timeout=5, memory=128, configured=False)  # no credentials, no dependencies

        CfnOutput(self, "SecretName", value=self.secret.secret_name, description="Fill it with infra/scripts/set_secrets.py")
