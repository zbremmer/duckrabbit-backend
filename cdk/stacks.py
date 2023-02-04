import dataclasses
from constructs import Construct
from aws_cdk import Stack, aws_iam, aws_lambda

from cdk import constructs


class DuckRabbitStack(constructs.CommonConstruct, Stack):
    def __init__(
        self,
        scope: Construct,
        cid: str,
        prefix: str = None,
        **kwargs,
    ):
        super().__init__(scope, cid, prefix, **kwargs)

        # Create Dynamo table
        self.dynamo = constructs.DynamoDB(self)

        # Create lambda authorizer TODO
        self.authorizer_role = ""
        self.authorizer = constructs.LambdaFunction(self)

        # Create API
        self.api = constructs.AppSync(
            self,
            f"{cid}-appsync",
            name=f"{self.prefix}-api",
            schema_path="schema/schema.graphql",
            authorizer=self.authorizer.function,
        )

        # Create IAM for dynamo access
        self.api_role = aws_iam.Role(
            self,
            "graphql-dynamo-role",
            assumed_by=aws_iam.ServicePrincipal("appsync.amazonaws.com"),
        )

        self.api_role.add_to_policy(
            aws_iam.PolicyStatement(
                resources=[self.dynamo.arn],  # TODO: Check this
                actions=[
                    "dynamodb:UpdateTable",
                    "dynamodb:Query",
                    "dynamodb:DescribeTable",
                    "dynamodb:BatchGetItem",
                    "dynamodb:GetItem",
                    "dynamodb:GetRecords",
                    "dynamodb:Scan",
                    "dynamodb:BatchWriteItem",
                    "dynamodb:ConditionCheckItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:PutItem",
                ],
            )
        )

        # Add data source
        self.datasource = self.api.add_data_source(
            # TODO: Add dynamo table
        )

        # Add template query resolverers
        self.api.add_template_resolvers(
            type_name="Query",
            datasource_name=self.datasource.attr_name,
            resolver_wildcard_path="src/template_resolvers/query/*_request.vtl",
        )

        # Add template mutation resolverers
        self.api.add_template_resolvers(
            type_name="Mutation",
            datasource_name=self.datasource.attr_name,
            resolver_wildcard_path="src/template_resolvers/mutation/*_request.vtl",
        )

        # Add lambda resolvers (create lambdas here if needed)
