import os
import glob
from pathlib import Path
from typing import Sequence

from constructs import Construct
from aws_cdk import (
    aws_appsync,
    aws_certificatemanager,
    aws_ec2,
    aws_iam,
    aws_lambda,
    CfnOutput,
    CustomResource,
    Duration,
    Fn,
    RemovalPolicy,
)


class CommonConstruct(Construct):
    def __init__(
        self, scope: Construct, cid: str, prefix: str = None, **kwargs
    ):
        super().__init__(scope, cid, **kwargs)
        self.prefix = prefix


class Networking(CommonConstruct):
    pass  # TODO need to figure this out....


class LambdaFunction(CommonConstruct):
    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        src: str,
        runtime: aws_lambda.Runtime = None,
        layers: Sequence[aws_lambda.ILayerVersion] = None,
        name: str = None,
        env_vars: dict = None,
        secret: str = None,
        role: aws_iam.Role = None,
        **kwargs,
    ):
        super().__init__(scope, cid, **kwargs)

        self.networking = Networking(self, "networking", name=name)
        self.name = name

        if secret:
            env_vars["secret"] = secret

        self.function = aws_lambda.Function(
            self,
            "lambda",
            code=get_asset(path=src),
            handler="handler.lambda_handler",
            runtime=runtime,
            layers=layers,
            vpc=self.networking.vpc,
            vpc_subnets=self.networking.subnets,
            security_groups=[self.networking.security_group],
            allow_public_subnet=False,
            environment=env_vars,
            function_name=name,
            timeout=Duration.minutes(15),
            memory_size=1024,
            role=role,
        )


class AppSync(CommonConstruct):
    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        name: str,
        schema_path: str,
        authorizer: aws_lambda.function,
        **kwargs,
    ):
        self.name = name
        # Create logging role
        self.log_role = aws_iam.Role(
            self,
            "log-role",
            assumed_by=aws_iam.ServicePrincipal("appsync.amazonaws.com"),
            managed_policies=[
                aws_iam.ManagedPolicy.from_managed_policy_arn(
                    self,
                    "appsync-cloudwatch-policy",
                    "arn:aws:iam::aws:policy/service-role/AWSAppSyncPushToCloudwatchLogs",
                )
            ],
        )

        # Create api
        self.api = aws_appsync.CfnGraphQLApi(
            self,
            "graphql-api",
            name=self.name,
            authentication_type="AWS_LAMBDA",
            lambda_authorizer_config=aws_appsync.CfnGraphQLApi.LambdaAuthorizerConfigProperty(
                authorizer_result_ttl_in_seconds=300,
                authorizer_uri=authorizer.function_arn,
            ),
            log_config=aws_appsync.CfnGraphQLApi.LogConfigProperty(
                cloud_watch_logs_role_arn=self.log_role.role_arn,
                exclude_verbose_content=False,
                field_log_level="ALL",
            ),
        )
        self.api.node.add_dependency(authorizer)

        # Schema
        self.schema = aws_appsync.CfnGraphQLSchema(
            self,
            "graphql-schema",
            api_id=self.api.attr_api_id,
            definition=get_asset(schema_path),
        )

    def add_data_source(
        self, table_name: str, region: str, iam_arn: str
    ) -> aws_appsync.CfnDataSource:
        return aws_appsync.CfnDataSource(
            self,
            "datasource",
            api_id=self.api.attr_api_id,
            name="graphqldatasource",
            type="AMAZON_DYNAMODB",
            dynamo_db_config=aws_appsync.CfnDataSource.DynamoDBConfigProperty(
                aws_region=region, table_name=table_name
            ),
            service_role_arn=iam_arn,
        )

    def add_template_resolvers(
        self,
        type_name: str,
        datasource_name: str,
        resolver_wildcard_path: str = None,
        kind: str = "UNIT",
        **kwargs,
    ) -> None:
        if not resolver_wildcard_path:
            resolver_wildcard_path = (
                f"src/template_resolvers/{type_name.lower()}/*_request.vtl"
            )

        resolver_list = [
            os.pathname.basename(file).split("_request.vtl")[0]
            for file in glob.glob(resolver_wildcard_path)
        ]

        if type_name.lower() not in ["query", "mutation"]:
            raise ValueError(f"Resolver type {type_name} is not valid.")

        for item in resolver_list:
            self.resolver = aws_appsync.CfnResolver(
                self,
                f"graphql-resolver-{item}",
                api_id=self.api.attr_api_id,
                field_name=item,
                type_name=type_name,
                data_source_name=datasource_name,
                kind=kind,
                request_mapping_template=get_resolver(
                    "template", type_name, f"{item}_request.vtl"
                ),
                response_mapping_template=get_resolver(
                    "template", type_name, f"{item}_response.vtl"
                ),
                **kwargs,
            )
            self.resolver.add_depends_on(self.api)
            self.resolver.add_depends_on(self.schema)

    def add_lambda_resolver(
        self, type_name: str, field_name: str, lambda_arn: str
    ):
        appsync_lambda_role = aws_iam.Role(
            self,
            f"graphql-resolver-role-{field_name}",
            assumed_by=aws_iam.ServicePrincipal("appsync.amazonaws.com"),
        )

        appsync_lambda_role.add_to_policy(
            aws_iam.PolicyStatement(
                resources=[lambda_arn], actions=["lambda:InvokeFunction"]
            )
        )

        # Add lambda as a data source
        resolver_datasource = aws_appsync.CfnDataSource(
            self,
            id=f"graphql-datasource-{field_name}",
            api_id=self.api.attr_api_id,
            name=f"graphqldatasource{field_name}",
            type="AWS_LAMBDA",
            lambda_config=aws_appsync.CfnDataSource.LambdaConfigProperty(
                lambda_function_arn=lambda_arn,
            ),
            service_role_arn=appsync_lambda_role.role_arn,
        )

        # Create Resolver
        self.lambda_direct_resolver = aws_appsync.CfnResolver(
            self,
            f"graphql-resolver-{field_name}",
            api_id=self.api.attr_api_id,
            field_name=field_name,
            type_name=type_name,
            kind="UNIT",
            data_source_name=resolver_datasource.attr_name,
            request_mapping_template=get_resolver(
                "lambda", type_name, f"{field_name}_request.vtl", field_name
            ),
            response_mapping_template=get_resolver(
                "lambda", type_name, f"{field_name}_response.vtl", field_name
            ),
        )
        self.lambda_direct_resolver.add_depend_on(self.api)
        self.lambda_direct_resolver.add_depend_on(self.schema)


def get_root() -> Path:
    path = Path(os.path.dirname(__file__)).resolve()
    while True:
        if "cdk.json" in os.listdir(path):
            return path
        if path == Path("/"):
            break
        path = path.parent
    return None


def get_asset(path: str, strip_newline: True) -> str:
    _full_path = str((get_root() / "src" / path).resolve())
    with open(_full_path, r) as f:
        return f.read().replace("\n", "") if strip_newline else f.read()


def get_resolver(
    resolver_type: str,
    graphql_type: str,
    fname: str,
    resolver_name: str = None,
) -> str:
    if resolver_type.lower() == "lambda":
        return get_asset(
            f"{resolver_type.lower()}_resolvers/{graphql_type.lower()}/{resolver_name}/mapping_templates/{fname}",
            strip_newline=False,
        )
    return get_asset(
        f"{resolver_type.lower()}_resolvers/{graphql_type.lower()}/{fname}",
        strip_newline=False,
    )
