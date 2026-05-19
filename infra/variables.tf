variable "project_name" {
  description = "Short identifier used to name AWS resources."
  type        = string
  default     = "aws-docs-agent"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,40}$", var.project_name))
    error_message = "project_name must be lowercase alphanumeric / dashes (start with a letter)."
  }
}

variable "environment" {
  description = "Deployment environment label (dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region. Bedrock model availability varies; us-east-1 / us-west-2 are safest."
  type        = string
  default     = "us-east-1"
}

variable "owner_email" {
  description = "Owner email used in resource tags."
  type        = string
}

variable "bedrock_chat_model_id" {
  description = <<-EOT
    Bedrock model ID for chat completions. For newer Anthropic models you
    typically want the `us.*` cross-region inference-profile ID, bare model
    IDs only work with provisioned throughput.
  EOT
  type        = string
  default     = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
}

variable "bedrock_embed_model_id" {
  description = "Bedrock model ID for embeddings."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "container_image_tag" {
  description = <<-EOT
    Tag of the container image to deploy. Push your image to the ECR repo
    created by this stack, then set this to that tag (e.g. "v1", "latest").
    Leave empty on first apply, the App Runner service is created with an
    explicit dependency on the image being present.
  EOT
  type        = string
  default     = "latest"
}

variable "app_runner_cpu" {
  description = "App Runner instance CPU. Allowed: 0.25 vCPU, 0.5 vCPU, 1 vCPU, 2 vCPU, 4 vCPU."
  type        = string
  default     = "1 vCPU"
}

variable "app_runner_memory" {
  description = "App Runner instance memory. Must be compatible with chosen CPU."
  type        = string
  default     = "2 GB"
}

variable "app_runner_min_instances" {
  description = "Minimum App Runner instances (1 keeps the index warm)."
  type        = number
  default     = 1
}

variable "app_runner_max_instances" {
  description = "Maximum App Runner instances."
  type        = number
  default     = 2
}

variable "deploy_app" {
  description = <<-EOT
    Two-phase apply switch:
      false: provisions ECR + S3 + IAM only (run this first).
      true:  also stands up the ECS service + ALB + CloudFront pointing at
             the image you pushed to ECR after phase 1.

    Avoids the "image not found" failure mode you hit if ECS tries to start
    a task against an empty ECR repo.
  EOT
  type        = bool
  default     = false
}
