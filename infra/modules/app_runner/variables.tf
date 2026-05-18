variable "service_name" {
  type        = string
  description = "App Runner service name."
}

variable "aws_region" {
  type = string
}

variable "image_repository_url" {
  type        = string
  description = "ECR repository URL (without tag)."
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "access_role_arn" {
  type        = string
  description = "IAM role for App Runner to pull from ECR."
}

variable "instance_role_arn" {
  type        = string
  description = "IAM role assumed by the running container."
}

variable "cpu" {
  type    = string
  default = "1 vCPU"
}

variable "memory" {
  type    = string
  default = "2 GB"
}

variable "min_instances" {
  type    = number
  default = 1
}

variable "max_instances" {
  type    = number
  default = 2
}

variable "bedrock_chat_model_id" {
  type = string
}

variable "bedrock_embed_model_id" {
  type = string
}

variable "index_s3_bucket" {
  type = string
}

variable "index_s3_prefix" {
  type    = string
  default = "faiss/"
}

variable "agent_max_tool_calls" {
  type    = number
  default = 6
}

variable "agent_top_k" {
  type    = number
  default = 6
}
