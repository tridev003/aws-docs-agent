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

# ---- IAM: workload + ECS execution roles -----------------------------------
module "iam" {
  source                 = "./modules/iam"
  name_prefix            = local.name_prefix
  aws_region             = var.aws_region
  bedrock_chat_model_id  = var.bedrock_chat_model_id
  bedrock_embed_model_id = var.bedrock_embed_model_id
  index_bucket_arn       = module.storage.bucket_arn
}

# ---- ECS Fargate + ALB: public chat UI -------------------------------------
# Gated by `deploy_app` (kept for backwards compat with the var name)
# so the first apply provisions ECR + S3 + IAM, the image is pushed, then a
# second apply spins up ECS pointing at the now-present image.
#
# Why ECS+ALB and not App Runner: App Runner's envoy proxy doesn't pass
# WebSocket upgrades, which Streamlit requires for its reactive UI.
module "ecs" {
  count  = var.deploy_app ? 1 : 0
  source = "./modules/ecs_alb"

  name_prefix            = local.name_prefix
  aws_region             = var.aws_region
  image_repository_url   = module.ecr.repository_url
  image_tag              = var.container_image_tag
  execution_role_arn     = module.iam.ecs_execution_role_arn
  task_role_arn          = module.iam.ecs_task_role_arn
  bedrock_chat_model_id  = var.bedrock_chat_model_id
  bedrock_embed_model_id = var.bedrock_embed_model_id
  index_s3_bucket        = module.storage.bucket_name
  index_s3_prefix        = local.index_prefix
}

# ---- CloudFront: HTTPS in front of the ALB ---------------------------------
# Lets us serve HTTPS without owning a domain (uses *.cloudfront.net with the
# AWS-managed cert). ALB stays on HTTP-only inside.
module "cdn" {
  count  = var.deploy_app ? 1 : 0
  source = "./modules/cdn"

  name_prefix  = local.name_prefix
  alb_dns_name = module.ecs[0].alb_dns_name
}
