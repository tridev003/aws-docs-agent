# App Runner service that fronts the Streamlit container. We pick App Runner
# (over ECS Fargate + ALB) because it has the simplest happy path for a single
# stateless container with a public URL, fits this take-home demo well.
#
# IMPORTANT: the referenced image tag must exist in ECR before this resource
# is created. The root module guards this behind a `create_app_runner` flag.

resource "aws_apprunner_auto_scaling_configuration_version" "this" {
  auto_scaling_configuration_name = "${var.service_name}-asg"
  min_size                        = var.min_instances
  max_size                        = var.max_instances
  max_concurrency                 = 50
}

resource "aws_apprunner_service" "this" {
  service_name                   = var.service_name
  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.this.arn

  source_configuration {
    auto_deployments_enabled = true

    authentication_configuration {
      access_role_arn = var.access_role_arn
    }

    image_repository {
      image_identifier      = "${var.image_repository_url}:${var.image_tag}"
      image_repository_type = "ECR"

      image_configuration {
        port = "8501"

        runtime_environment_variables = {
          AWS_REGION             = var.aws_region
          BEDROCK_CHAT_MODEL_ID  = var.bedrock_chat_model_id
          BEDROCK_EMBED_MODEL_ID = var.bedrock_embed_model_id
          INDEX_S3_BUCKET        = var.index_s3_bucket
          INDEX_S3_PREFIX        = var.index_s3_prefix
          INDEX_LOCAL_PATH       = "/app/data/index"
          AGENT_MAX_TOOL_CALLS   = tostring(var.agent_max_tool_calls)
          AGENT_TOP_K            = tostring(var.agent_top_k)
          PYTHONUNBUFFERED       = "1"
        }
      }
    }
  }

  instance_configuration {
    cpu               = var.cpu
    memory            = var.memory
    instance_role_arn = var.instance_role_arn
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/_stcore/health"
    interval            = 20
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  network_configuration {
    egress_configuration {
      egress_type = "DEFAULT" # public egress; no VPC connector needed for Bedrock + S3
    }

    ingress_configuration {
      is_publicly_accessible = true
    }
  }

  observability_configuration {
    observability_enabled = false # turn on if you wire X-Ray
  }
}
