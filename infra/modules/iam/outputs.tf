output "access_role_arn" {
  value       = aws_iam_role.access.arn
  description = "App Runner ECR-access role ARN (legacy, App Runner path)."
}

output "instance_role_arn" {
  value       = aws_iam_role.instance.arn
  description = "Workload role ARN: assumed by App Runner instances and ECS Fargate tasks. Holds Bedrock + S3 perms."
}

output "ecs_execution_role_arn" {
  value       = aws_iam_role.ecs_execution.arn
  description = "ECS task execution role: ECR pull + CloudWatch logs."
}

output "ecs_task_role_arn" {
  value       = aws_iam_role.ecs_task.arn
  description = "ECS task role: Bedrock + S3 perms for the running container."
}
