locals {
  name_prefix  = "${var.project_name}-${var.environment}"
  index_prefix = "faiss/"
}

# ---- storage: FAISS index bucket -------------------------------------------
module "storage" {
  source      = "./modules/storage"
  name_prefix = local.name_prefix
}

# ---- ECR: image registry ---------------------------------------------------
module "ecr" {
  source          = "./modules/ecr"
  repository_name = var.project_name
}

# ---- IAM: roles for App Runner ---------------------------------------------
module "iam" {
  source                 = "./modules/iam"
  name_prefix            = local.name_prefix
  aws_region             = var.aws_region
  bedrock_chat_model_id  = var.bedrock_chat_model_id
  bedrock_embed_model_id = var.bedrock_embed_model_id
  index_bucket_arn       = module.storage.bucket_arn
}

# ---- App Runner: public Streamlit service ----------------------------------
# Gated by `create_app_runner` so the first `terraform apply` can provision
# ECR + S3 + IAM, the user pushes an image, then a second apply turns the
# service on. Avoids the "image not found" failure mode.
module "app_runner" {
  count  = var.create_app_runner ? 1 : 0
  source = "./modules/app_runner"

  service_name           = "${local.name_prefix}-svc"
  aws_region             = var.aws_region
  image_repository_url   = module.ecr.repository_url
  image_tag              = var.container_image_tag
  access_role_arn        = module.iam.access_role_arn
  instance_role_arn      = module.iam.instance_role_arn
  cpu                    = var.app_runner_cpu
  memory                 = var.app_runner_memory
  min_instances          = var.app_runner_min_instances
  max_instances          = var.app_runner_max_instances
  bedrock_chat_model_id  = var.bedrock_chat_model_id
  bedrock_embed_model_id = var.bedrock_embed_model_id
  index_s3_bucket        = module.storage.bucket_name
  index_s3_prefix        = local.index_prefix
}
