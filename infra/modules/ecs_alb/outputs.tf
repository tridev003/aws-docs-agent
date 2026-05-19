output "service_url" {
  value       = "http://${aws_lb.this.dns_name}"
  description = "Public URL for the chat UI."
}

output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "service_name" {
  value = aws_ecs_service.this.name
}

output "log_group" {
  value = aws_cloudwatch_log_group.task.name
}
