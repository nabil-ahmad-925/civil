#!/usr/bin/env python3
"""Puts the stacks together. Each stack is one file in stacks/; this file only creates them and passes what one
stack needs from another.

    LambdaStack ──(function ARNs)──> ApiGatewayStack

Deploy both with `cdk deploy --all`; CDK orders them by that link.
"""

import os

import aws_cdk as cdk

from stacks.api_gateway_stack import ApiGatewayStack
from stacks.lambda_stack import LambdaStack

app = cdk.App()

# The account and region come from the AWS profile in use (the CDK command line sets these two variables).
# To pin the app to one account, write the values here instead.
env = cdk.Environment(account=os.getenv("CDK_DEFAULT_ACCOUNT"), region=os.getenv("CDK_DEFAULT_REGION"))

lambda_stack = LambdaStack(app, "CivilEngLambda", env=env, description="Chat and health Lambdas and their secret.")

ApiGatewayStack(
    app, "CivilEngApi", env=env,
    chat_arn=lambda_stack.chat.function_arn, health_arn=lambda_stack.health.function_arn,
    description="HTTP API in front of the Lambdas.",
)

app.synth()
