output "ecr_repository_url" {
  value       = module.ecr.repository_url
  description = "Push images to: docker push <this>:<tag>"
}

output "index_bucket" {
  value       = module.storage.bucket_name
  description = "S3 bucket holding the FAISS index. Set INDEX_S3_BUCKET to this."
}

output "index_prefix" {
  value       = local.index_prefix
  description = "S3 key prefix under which faiss.index / metadata.jsonl / manifest.json live."
}

output "app_runner_url" {
  value       = try(module.app_runner[0].service_url, null)
  description = "Public URL of the deployed Streamlit app (null until create_app_runner=true)."
}

output "next_steps" {
  value = <<-EOT
    Next steps after the first apply:

    1) Build & push the container image:
         make push-image

    2) Run ingestion locally and upload the index to the bucket:
         INDEX_S3_BUCKET=${module.storage.bucket_name} make ingest

    3) Re-apply with the App Runner gate on:
         terraform apply -var "create_app_runner=true" -var "owner_email=${var.owner_email}"

    The service URL will show up as the `app_runner_url` output.
  EOT
}
