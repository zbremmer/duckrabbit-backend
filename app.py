from aws_cdk import App
from cdk.stacks import S3ObjectLambdaStack

app = App()
S3ObjectLambdaStack(app, "S3ObjectLambdaExample")
app.synth()
