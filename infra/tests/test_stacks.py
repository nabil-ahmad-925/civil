import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from stacks.api_gateway_stack import ApiGatewayStack
from stacks.lambda_stack import LambdaStack

ENV = cdk.Environment(account="111111111111", region="us-east-1")


def build(**context):
    """The same wiring as app.py, without building the Lambda dependencies."""
    app = cdk.App(context={"bundle": "false", **context})
    fns = LambdaStack(app, "Lam", env=ENV)
    api = ApiGatewayStack(app, "Api", env=ENV, chat_arn=fns.chat.function_arn, health_arn=fns.health.function_arn)
    return {"lam": Template.from_stack(fns), "api": Template.from_stack(api), "app": app}


def test_the_two_stacks_synthesize_without_a_dependency_cycle():
    assembly = build()["app"].synth()  # raises on a cycle
    deps = {s.stack_name: {d.id for d in s.dependencies if not d.id.endswith(".assets")} for s in assembly.stacks}
    assert deps == {"Lam": set(), "Api": {"Lam"}}


def test_two_arm_python_lambdas_and_only_chat_gets_the_secret():
    t = build()["lam"]
    for name in ("chat", "health"):
        t.has_resource_properties("AWS::Lambda::Function", {"FunctionName": f"civil-eng-{name}", "Handler": "handler.handler", "Runtime": "python3.13", "Architectures": ["arm64"]})
    t.has_resource_properties("AWS::Lambda::Function", {"FunctionName": "civil-eng-chat", "Timeout": 60})
    functions = t.find_resources("AWS::Lambda::Function")
    health = next(v for v in functions.values() if v["Properties"]["FunctionName"] == "civil-eng-health")
    assert "Environment" not in health["Properties"]
    t.resource_count_is("AWS::Lambda::Function", 2)


def test_the_secret_generates_only_the_api_key_and_no_credential_is_in_the_templates():
    built = build()
    built["lam"].has_resource_properties("AWS::SecretsManager::Secret", {"Name": "civil-eng/chat", "GenerateSecretString": Match.object_like({"GenerateStringKey": "CHAT_API_KEY"})})
    assert all("sk-" not in str(built[k].to_json()) for k in ("lam", "api"))


def test_api_routes_cors_throttling_and_permissions_live_in_the_api_stack():
    t = build(cors_origins="https://app.example.com,http://localhost:3000")["api"]
    for route in ("POST /chat", "GET /health"):
        t.has_resource_properties("AWS::ApiGatewayV2::Route", {"RouteKey": route})
    t.resource_count_is("AWS::ApiGatewayV2::Route", 2)
    t.has_resource_properties("AWS::ApiGatewayV2::Api", {"CorsConfiguration": Match.object_like({"AllowOrigins": ["https://app.example.com", "http://localhost:3000"], "AllowMethods": Match.array_with(["POST"])})})
    t.has_resource_properties("AWS::ApiGatewayV2::Stage", {"DefaultRouteSettings": {"ThrottlingRateLimit": 10, "ThrottlingBurstLimit": 20}})
    t.resource_count_is("AWS::Lambda::Permission", 2)  # created here, which is what avoids the cycle
