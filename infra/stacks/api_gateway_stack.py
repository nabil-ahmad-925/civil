from aws_cdk import CfnOutput, Duration, Stack, Tags
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


def _list(value) -> list[str]:  # noqa: ANN001
    """A context value given as a list (cdk.json) or as "a,b" (-c cors_origins=a,b)."""
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return list(value or [])


class ApiGatewayStack(Stack):
    """The public HTTP API: routes to the two functions, CORS and a rate limit.

    /chat needs the x-api-key header; the function checks it. /health is open.
    """

    def __init__(
        self, scope: Construct, construct_id: str, *, chat_arn: str, health_arn: str, **kwargs  # noqa: ANN003
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        ctx = self.node.try_get_context
        origins = _list(ctx("cors_origins")) or ["http://localhost:3000"]

        # Imported by ARN so the permission that lets API Gateway call a function is created in THIS stack.
        # Passing the Function objects themselves would make the two stacks depend on each other.
        def fn(name: str, arn: str) -> lambda_.IFunction:
            return lambda_.Function.from_function_attributes(self, name, function_arn=arn, same_environment=True)

        api = apigw.HttpApi(
            self, "Api", api_name="civil-eng-chat", create_default_stage=False,
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=origins,
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.OPTIONS],
                allow_headers=["content-type", "x-api-key"], max_age=Duration.hours(1),
            ),
        )
        api.add_routes(path="/chat", methods=[apigw.HttpMethod.POST], integration=integrations.HttpLambdaIntegration("ChatIntegration", fn("Chat", chat_arn)))
        api.add_routes(path="/health", methods=[apigw.HttpMethod.GET], integration=integrations.HttpLambdaIntegration("HealthIntegration", fn("Health", health_arn)))

        stage = apigw.HttpStage(
            self, "Stage", http_api=api, stage_name="$default", auto_deploy=True,
            throttle=apigw.ThrottleSettings(rate_limit=ctx("api_throttle_rate") or 10, burst_limit=ctx("api_throttle_burst") or 20),
        )
        Tags.of(self).add("app", "civil-eng")
        CfnOutput(self, "ApiUrl", value=stage.url or api.api_endpoint, description="Base URL of the chat API (CHAT_API_URL in the client)")
