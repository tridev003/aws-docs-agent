output "service_arn" {
  value       = aws_apprunner_service.this.arn
  description = "ARN of the App Runner service."
}

output "service_url" {
  value       = "https://${aws_apprunner_service.this.service_url}"
  description = "Public URL of the deployed Streamlit app."
}
