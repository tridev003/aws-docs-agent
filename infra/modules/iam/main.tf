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

data "aws_iam_policy_document" "instance" {
  # Bedrock, scoped to the two model IDs the app actually invokes. If you
  # swap models, update var.bedrock_*_model_id and re-apply.
  statement {
    sid    = "BedrockInvoke"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_chat_model_id}",
      "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_embed_model_id}",
      # Cross-region inference profile (used by some newer Anthropic models).
      "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/*",
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
