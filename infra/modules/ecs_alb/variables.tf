variable "name_prefix" {
  type        = string
  description = "Prefix for resource names."
}

variable "aws_region" {
  type = string
}

variable "image_repository_url" {
  type = string
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "execution_role_arn" {
  type        = string
  description = "ECS task execution role (ECR pull + CloudWatch logs)."
}

variable "task_role_arn" {
  type        = string
  description = "ECS task role (Bedrock + S3 perms for the running container)."
}

variable "cpu" {
  type    = number
  default = 1024 # 1 vCPU
}

variable "memory" {
  type    = number
  default = 2048 # 2 GB
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "container_port" {
  type    = number
  default = 8501
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
  default = 10
}

variable "agent_top_k" {
  type    = number
  default = 6
}
