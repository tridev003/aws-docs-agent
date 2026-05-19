output "url" {
  value       = "https://${aws_cloudfront_distribution.this.domain_name}"
  description = "Public HTTPS URL of the chat UI (via CloudFront)."
}

output "distribution_id" {
  value = aws_cloudfront_distribution.this.id
}

output "domain_name" {
  value = aws_cloudfront_distribution.this.domain_name
}
