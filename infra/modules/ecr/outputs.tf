output "repository_url" {
  value       = aws_ecr_repository.this.repository_url
  description = "Push images to this URL."
}

output "repository_arn" {
  value       = aws_ecr_repository.this.arn
  description = "ARN of the ECR repo (used by IAM)."
}

output "repository_name" {
  value       = aws_ecr_repository.this.name
  description = "Name of the ECR repo."
}
