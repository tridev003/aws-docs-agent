variable "name_prefix" {
  description = "Prefix for IAM role names."
  type        = string
}

variable "aws_region" {
  description = "Region where Bedrock model ARNs are scoped."
  type        = string
}

variable "bedrock_chat_model_id" {
  description = "Bedrock chat model ID to grant InvokeModel on."
  type        = string
}

variable "bedrock_embed_model_id" {
  description = "Bedrock embedding model ID to grant InvokeModel on."
  type        = string
}

variable "index_bucket_arn" {
  description = "ARN of the S3 bucket holding the FAISS index."
  type        = string
}
