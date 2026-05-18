output "access_role_arn" {
  value       = aws_iam_role.access.arn
  description = "App Runner ECR-access role ARN."
}

output "instance_role_arn" {
  value       = aws_iam_role.instance.arn
  description = "App Runner instance role ARN (Bedrock + S3 perms)."
}
