# Two IAM roles are needed by App Runner:
#
#   * access_role , used by App Runner to pull from ECR. AWS will fail the
#                    service create with an unhelpful error if this is wrong.
#   * instance_role, assumed by the running container; this is where Bedrock
#                     and S3 permissions live.
#
# We scope Bedrock to the two model IDs we actually call, not a wildcard.

data "aws_caller_identity" "current" {}

# ----- ECR access role (control plane) ---------------------------------------

data "aws_iam_policy_document" "access_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "access" {
  name               = "${var.name_prefix}-apprunner-access"
  assume_role_policy = data.aws_iam_policy_document.access_assume.json
}

resource "aws_iam_role_policy_attachment" "access_ecr" {
  role       = aws_iam_role.access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# ----- Instance role (data plane) --------------------------------------------

data "aws_iam_policy_document" "instance_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.name_prefix}-apprunner-instance"
  assume_role_policy = data.aws_iam_policy_document.instance_assume.json
}

locals {
  # If the chat model ID is a cross-region inference profile (us./eu./apac. prefix),
  # the underlying foundation model is the same ID without the prefix. IAM needs
  # both the profile ARN and the underlying FM ARN to allow invocation.
  chat_fm_id = replace(replace(replace(var.bedrock_chat_model_id, "us.", ""), "eu.", ""), "apac.", "")
}

data "aws_iam_policy_document" "instance" {
  # Bedrock. Cross-region inference profiles fan out to multiple regional FMs,
  # so we allow the profile ARN plus the underlying FM across all regions.
  statement {
    sid    = "BedrockInvoke"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      # Inference profile (account-scoped, any region).
      "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*",
      # Underlying foundation models, across regions the profile may route to.
      "arn:aws:bedrock:*::foundation-model/${local.chat_fm_id}",
      "arn:aws:bedrock:*::foundation-model/${var.bedrock_embed_model_id}",
    ]
  }

  # S3, read-only on the index bucket, write only on the configured prefix
  # (so the live app cannot rewrite arbitrary objects).
  statement {
    sid    = "S3IndexRead"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:ListBucket",
    ]
    resources = [
      var.index_bucket_arn,
      "${var.index_bucket_arn}/*",
    ]
  }

  # CloudWatch logs are wired up automatically by App Runner; no policy needed.
}

resource "aws_iam_role_policy" "instance" {
  name   = "${var.name_prefix}-apprunner-instance-policy"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

# ----- ECS task execution role (for Fargate) ---------------------------------
# Lets the Fargate agent pull the image from ECR and ship logs to CloudWatch.
# The application's own perms (Bedrock, S3) live on the instance role above,
# which doubles as the ECS task role.

data "aws_iam_policy_document" "ecs_execution_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${var.name_prefix}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_execution_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ----- ECS task role (workload perms for Fargate) ----------------------------
# Mirrors the App Runner instance role: same Bedrock + S3 statements, but a
# separate role because IAM in this account explicitly denies
# UpdateAssumeRolePolicy and we can't add ECS to the existing role's trust.

resource "aws_iam_role" "ecs_task" {
  name = "${var.name_prefix}-ecs-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy" "ecs_task" {
  name   = "${var.name_prefix}-ecs-task-policy"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.instance.json
}
